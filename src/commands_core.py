import asyncio
from permissions import Permissions, get_permissions
from channel_type import ChannelType, get_channel_type
from challonge_accounts import ChallongeAccess, UserNotFound, UserNameNotSet, APIKeyNotSet, InvalidCredentials, get as get_account
from challonge_utils import validate_tournament_state
from db_access import db
import discord
from const import *
import utils
from profiling import Profiler, Scope, profile, profile_async


commandFormat = '| {0:17}| {1:16}| {2:13}| {3:11}| {4:20}| {5:20}| {6:20}|'


class MissingParameters(Exception):
    def __init__(self, req, given):
        self.req = req
        self.given = given

    def __str__(self):
        return T_ValidateCommandContext_BadParameters.format(self.req, self.given)


class WrongChannel(Exception):
    def __str__(self):
        return T_ValidateCommandContext_BadChannel


class InsufficientPrivileges(Exception):
    def __str__(self):
        return T_ValidateCommandContext_BadPrivileges


class BadTournamentState(Exception):
    def __str__(self):
        return T_ValidateCommandContext_BadTournamentState


class Attributes:
    def __init__(self, **kwargs):
        self.minPermissions = kwargs.get('minPermissions', Permissions.User)
        self.channelRestrictions = kwargs.get('channelRestrictions', ChannelType.Other)
        self.challongeAccess = kwargs.get('challongeAccess', ChallongeAccess.NotRequired)
        self.tournamentState = kwargs.get('tournamentState', None)


class Command:
    def __init__(self, name, cb, attributes=None):
        self.name = name
        self.cb = cb
        self.attributes = attributes
        self.aliases = []
        self.reqParams = []
        self.optParams = []
        self.helpers = []

    def __repr__(self):
        return '[Command:%s]' % self.name

    def add_required_params(self, *args):
        self.reqParams = args
        return self

    def add_optional_params(self, *args):
        self.optParams = args
        return self

    def add_aliases(self, *args):
        self.aliases = args
        return self

    def add_helpers(self, *args):
        self.helpers = args
        return self

    async def validate_context(self, client, message, postCommand):
        if get_permissions(message.author, message.channel) < self.attributes.minPermissions:
            return False, InsufficientPrivileges()

        if not get_channel_type(message.channel) & self.attributes.channelRestrictions:
            return False, WrongChannel()

        if self.attributes.challongeAccess == ChallongeAccess.RequiredForAuthor:
            acc, exc = await get_account(message.author.id)
            if exc:
                return False, exc
        elif self.attributes.challongeAccess == ChallongeAccess.RequiredForHost:
            db_t = db.get_tournament(message.channel)
            acc, exc = await get_account(db_t.host_id)
            if exc:
                return False, exc
            if acc and self.attributes.tournamentState:
                if not await validate_tournament_state(acc, db_t.challonge_id, self.attributes.tournamentState):  # can raise
                    return False, BadTournamentState()

        reqParamsExpected = len(self.reqParams)
        givenParams = len(postCommand)
        if givenParams < reqParamsExpected:
            return False, MissingParameters(reqParamsExpected, givenParams)

        return True, None

    def validate_name(self, name):
        if self.name == name:
            return True
        elif self.aliases is not None:
            return name in self.aliases
        return False

    async def _fetch_helpers(self, message, postCommand):
        kwargs = {}
        for x in self.helpers:
            if x == 'account':
                if self.attributes.challongeAccess == ChallongeAccess.RequiredForAuthor:
                    kwargs[x], exc = await get_account(message.author.id)
                else:
                    kwargs[x], exc = await get_account(db.get_tournament(message.channel).host_id)
            elif x == 'tournament_id':
                kwargs[x] = db.get_tournament(message.channel).challonge_id
            elif x == 'tournament_role':
                roleid = db.get_tournament(message.channel).role_id
                kwargs[x] = discord.utils.find(lambda r: r.id == roleid, message.server.roles)
            elif x == 'tournament_channel':
                channelid = db.get_tournament(message.channel).channel_id
                kwargs[x] = discord.utils.find(lambda c: c.id == channelid, message.server.channels)
            elif x == 'participant_username':
                kwargs[x] = db.get_user(message.author.id).user_name
            elif x == 'announcement':
                kwargs[x] = ' '.join(postCommand)

        return kwargs

    def _fetch_args(self, postCommand):
        kwargs = {}

        for count, x in enumerate(self.reqParams):
            kwargs[x] = postCommand[count]
        offset = len(self.reqParams)

        for count, x in enumerate(self.optParams):
            if count + offset < len(postCommand):
                kwargs[x] = postCommand[count + offset]

        return kwargs

    async def execute(self, client, message, postCommand):
        kwargs = {}
        kwargs.update(self._fetch_args(postCommand))
        kwargs.update(await self._fetch_helpers(message, postCommand))
        await self.cb(client, message, **kwargs)

    def pretty_print(self):
        return self.simple_print() + '\n```{0}{1}```'.format('' if self.cb.__doc__ is None else self.cb.__doc__,
                                                             'No aliases' if len(self.aliases) == 0 else 'Aliases: ' + ' / '.join(self.aliases))

    def simple_print(self):
        return '`{0}` {1}{2} -- *{3}*'.format(self.name,
                                              '' if len(self.reqParams) == 0 else ' '.join(['[' + p + ']' for p in self.reqParams]),
                                              '' if len(self.optParams) == 0 else ' '.join(['{' + p + '}' for p in self.optParams]),
                                              'No description available' if self.cb.__doc__ is None else self.cb.__doc__.splitlines()[0])


class CommandsHandler:
    def __init__(self):
        self._commands = []

    def _add(self, command):
        self._commands.append(command)
        return command

    def find(self, name):
        for command in self._commands:
            if command.validate_name(name):
                return command
        return None

    def register(self, **attributes):
        def decorator(func):
            async def wrapper(client, message, **postCommand):
                # choose only those that are most likely arguments but not the api key (could be Account...)
                args = ' '.join([v for k, v in postCommand.items() if isinstance(v, str) and k != 'key'])
                # server for profiling info
                server = 0 if message.channel.is_private else message.channel.server.id
                with Profiler(Scope.Command, name=func.__name__, args=args, server=server) as p:
                    await func(client, message, **postCommand)
            wrapper.__doc__ = func.__doc__
            return self._add(Command(func.__name__, wrapper, Attributes(**attributes)))
        return decorator

    def _get_command_and_postcommand(self, client, message):
        split = message.content.split()
        if len(split) == 0:
            return None, None

        command = None

        if message.channel.is_private:
            command = self.find(split[0])
            offset = 1
        else:
            commandTrigger = db.get_server(message.server).trigger
            if len(split) <= 1:
                return None, None
            if split[0] == commandTrigger or client.user in message.mentions:
                if len(split) > 1:
                    command = self.find(split[1])
                    offset = 2

        if command:
            return command, split[offset:len(split)]
        else:
            return None, None

    async def try_execute(self, client, message):
        command, postCommand = self._get_command_and_postcommand(client, message)

        if command:
            try:
                validated, exc = await command.validate_context(client, message, postCommand)
            except Exception as e:
                print('[CommandsHandler.try_execute] [message: {0}] [Exception: {1}]'.format(message.content, e))
            else:
                if exc:
                    await client.send_message(message.channel, exc)
                elif validated:
                    await command.execute(client, message, postCommand)
                    print(T_Log_ValidatedCommand.format(command.name,
                                                        '' if len(postCommand) == 0 else ' ' + ' '.join(postCommand),
                                                        message,
                                                        'PM' if message.channel.is_private else '{0.channel.server.name}/#{0.channel.name}'.format(message)))

    def dump(self):
        return utils.print_array('Commands Registered',
                                 commandFormat.format('Name', 'Min Permissions', 'Channel Type', 'Challonge', 'Aliases', 'Required Args', 'Optional Args'),
                                 self._commands,
                                 lambda c: commandFormat.format(c.name,
                                                                c.attributes.minPermissions.name,
                                                                c.attributes.channelRestrictions.name,
                                                                c.attributes.challongeAccess.name,
                                                                '-' if len(c.aliases) == 0 else '/'.join(c.aliases),
                                                                '-' if len(c.reqParams) == 0 else '/'.join(c.reqParams),
                                                                '-' if len(c.optParams) == 0 else '/'.join(c.optParams)))


commands = CommandsHandler()


class AuthorizedCommandsWrapper:
    def __init__(self, client, message):
        self._client = client
        self._message = message
        self._commands = iter(commands._commands)

    async def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            command = next(self._commands)
        except StopIteration:
            raise StopAsyncIteration
        else:
            validated, exc = await command.validate_context(self._client, self._message, [])
            if validated or isinstance(exc, MissingParameters):
                return command.simple_print()
            else:
                return await self.__anext__()


def required_args(*args):
    def decorator(func):
        return func.add_required_params(*args)
    return decorator


def optional_args(*args):
    def decorator(func):
        return func.add_optional_params(*args)
    return decorator


def aliases(*args):
    def decorator(func):
        return func.add_aliases(*args)
    return decorator


def helpers(*args):
    def decorator(func):
        return func.add_helpers(*args)
    return decorator

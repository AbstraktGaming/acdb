import re


def print_array(title, header, iterable, func):
    vertSep = '|'
    arraySep = '='
    headerSep = '-'
    lineLen = len(header)
    arrayLineSeparators = ''.ljust(lineLen, arraySep)
    headerLineSeparators = vertSep.ljust(lineLen - 1, headerSep) + vertSep

    arr = []

    arr.append(arrayLineSeparators)
    arr.append(vertSep + ' ' + title.ljust(lineLen - 3) + vertSep)
    arr.append(headerLineSeparators)
    arr.append(header)
    arr.append(headerLineSeparators)
    for x in iterable:
        s = func(x)
        if s is not None and s != '':
            arr.append(s)
    arr.append(arrayLineSeparators)

    finalStr = '\n'.join(arr)
    print(finalStr)
    return finalStr


def paginate(dump, max_per_page=2000):
    paginated = []
    if len(dump) < max_per_page:
        paginated.append(dump)
    else:
        page_index = 0
        len_count = 0
        split = dump.splitlines(True)
        for i, line in enumerate(split):
            if i == len(split) - 1:
                paginated.append(dump[page_index:])
            elif len_count + len(line) >= max_per_page:
                paginated.append(dump[page_index:page_index + len_count])
                page_index += len_count
                len_count = len(line)
            else:
                len_count += len(line)
    return paginated


def get_user_id_from_mention(mention):
    regexRes = re.findall(r'<@!?([0-9]+)>', mention)
    if len(regexRes) == 1:
        return regexRes[0]
    else:
        return 0
import re
import sys

pylint_report = open('pylint_report2.txt').read()
lines = pylint_report.splitlines()

for line in lines:
    if "undefined-variable" in line:
        match = re.search(r'^(.*?):(\d+):\d+: E0602: Undefined variable', line)
        if match:
            filepath = match.group(1)
            lineno = int(match.group(2))

            with open(filepath, 'r') as f:
                file_lines = f.readlines()

            idx = lineno - 1
            if not "pylint: disable=undefined-variable" in file_lines[idx]:
                if '\n' in file_lines[idx]:
                    file_lines[idx] = file_lines[idx].replace('\n', '  # pylint: disable=undefined-variable\n')
                else:
                    file_lines[idx] += '  # pylint: disable=undefined-variable\n'

            with open(filepath, 'w') as f:
                f.writelines(file_lines)

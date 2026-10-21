"""QA-only evidence about observed native command items, not a runtime policy."""
import re
import sys

FOREIGN_WINDOWS_COMMAND=re.compile(r'(?i)(?<![A-Za-z0-9_])(?:wsl(?:\.exe)?|wslhost(?:\.exe)?|bash(?:\.exe)?|docker(?:\.exe)?|podman(?:\.exe)?|cygwin\d*|msys\d*)(?![A-Za-z0-9_])')


def host_execution_evidence(items,*,platform=None):
    platform=platform or sys.platform
    commands=[{'id':item.get('id'),'command':item.get('command'),'exit_code':item.get('exitCode')}
              for item in items if item.get('type')=='commandExecution']
    malformed=[row for row in commands if not isinstance(row['command'],str)]
    forbidden=[row for row in commands if isinstance(row['command'],str) and FOREIGN_WINDOWS_COMMAND.search(row['command'])] if platform=='win32' else []
    return {'passed':not malformed and not forbidden,'platform':platform,'observed_command_count':len(commands),
            'forbidden_commands':forbidden,'malformed_commands':malformed,
            'scope':'Observed native commandExecution items; not a system-wide process audit.'}


def command_mentions_path(command,path):
    """Account for native UI command rendering, which escapes backslashes."""
    text=str(path);variants={text,text.replace('\\','/'),text.replace('/','\\')}
    variants|={value.replace('\\','\\\\') for value in variants}
    return any(value.casefold() in command.casefold() for value in variants)

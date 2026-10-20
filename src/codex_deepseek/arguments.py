"""Owning command discovery for the pinned native clap parser."""
VALUE_OPTIONS={'-c','--config','-C','--cd','-p','--profile','-m','--model','-s','--sandbox',
               '-i','--image','--add-dir','--enable','--disable','--remote','--remote-auth-token-env',
               '--local-provider','-a','--ask-for-approval'}
APP_VALUE_OPTIONS=VALUE_OPTIONS|{'--listen','--auth-token-env','--auth-token-file','--code-mode-host-path'}


def command_index(arguments,*,start=0,values=VALUE_OPTIONS):
    index=start
    while index<len(arguments):
        token=arguments[index]
        if token=='--':return None
        if not token.startswith('-'):return index
        index+=2 if token in values else 1
    return None


def app_server_command(arguments):
    """Find app-server management scopes while skipping native option values."""
    owner=command_index(arguments)
    if owner is None or arguments[owner]!='app-server':return []
    rest=arguments[owner+1:]
    child=command_index(rest,values=APP_VALUE_OPTIONS)
    if child is None:return []
    rest=rest[child:]
    if rest[0]=='daemon':
        nested=command_index(rest[1:],values=APP_VALUE_OPTIONS)
        return ['daemon',*rest[nested+1:]] if nested is not None else ['daemon']
    return rest

"""An installer upgrade must not erase native Windows sandbox traversal grants."""
import os
import pytest
pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows native ACL contract')


def _security(path):
    import win32security
    sd=win32security.GetNamedSecurityInfo(str(path),win32security.SE_FILE_OBJECT,
        win32security.OWNER_SECURITY_INFORMATION|win32security.DACL_SECURITY_INFORMATION)
    return win32security.ConvertSecurityDescriptorToStringSecurityDescriptor(sd,1,
        win32security.OWNER_SECURITY_INFORMATION|win32security.DACL_SECURITY_INFORMATION)


def _add(path, mask, flags=0):
    import win32security
    sd=win32security.GetNamedSecurityInfo(str(path),win32security.SE_FILE_OBJECT,win32security.DACL_SECURITY_INFORMATION)
    acl=sd.GetSecurityDescriptorDacl()
    acl.AddAccessAllowedAceEx(win32security.ACL_REVISION,flags,mask,win32security.ConvertStringSidToSid('S-1-5-11'))
    win32security.SetNamedSecurityInfo(str(path),win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION|win32security.PROTECTED_DACL_SECURITY_INFORMATION,None,None,acl,None)


@pytest.mark.parametrize('mask',[0xa0,0x100080])
def test_native_package_keeps_existing_noninheriting_metadata_grant(tmp_path,mask):
    from test_install import installer,archive_at
    root=tmp_path/'install';root.mkdir();installer.private_directory(root)
    source=tmp_path/'native.tar.gz'
    manifest=archive_at(source,{name:b'fixture' for name in installer.required_binaries()})
    installer.native_package(root,manifest,source)
    (root/'installation.json').write_text('{}',encoding='utf8')
    _add(root,mask);before=_security(root)
    installer.native_package(root,manifest,source)
    assert _security(root)==before


@pytest.mark.parametrize('mask,flags',[(0x2,0),(0xa0,3)])
@pytest.mark.parametrize('receipt',[True,False],ids=['active-receipt','inactive-state'])
def test_existing_managed_root_refuses_unknown_grants_without_rewriting(tmp_path,mask,flags,receipt):
    from test_install import installer
    root=tmp_path/'install';installer.private_install_root(root)
    if receipt:(root/'installation.json').write_text('{}',encoding='utf8')
    else:(root/'releases').mkdir()
    _add(root,mask,flags);before=_security(root)
    with pytest.raises(PermissionError,match='installation root'):
        installer.private_install_root(root)
    assert _security(root)==before


@pytest.mark.parametrize('mode',['reuse','reuse-with-archive','new-package'])
def test_native_container_keeps_existing_metadata_grant(tmp_path,mode):
    from test_install import installer,archive_at
    root=tmp_path/'install';source=tmp_path/'native.tar.gz'
    files={name:b'first' for name in installer.required_binaries()}
    manifest=archive_at(source,files);installer.native_package(root,manifest,source)
    container=root/'native';_add(container,0x100080);before=_security(container)
    if mode=='new-package':
        source=tmp_path/'second.tar.gz'
        manifest=archive_at(source,{name:b'second' for name in installer.required_binaries()})
    installer.native_package(root,manifest,None if mode=='reuse' else source)
    assert _security(container)==before


def test_native_container_rejects_unknown_grant_without_rewriting(tmp_path):
    from test_install import installer,archive_at
    root=tmp_path/'install';source=tmp_path/'native.tar.gz'
    manifest=archive_at(source,{name:b'fixture' for name in installer.required_binaries()})
    installer.native_package(root,manifest,source)
    container=root/'native';_add(container,0x2);before=_security(container)
    with pytest.raises(PermissionError):installer.native_package(root,manifest)
    assert _security(container)==before

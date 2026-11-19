"""Synthetic Files provider can create then return 4xx; never calls OpenRouter."""
import json

import pytest
from codex_deepseek.image_transport import FileOperationError,OpenRouterFilesBackend,UploadOutcomeUnknown,_FileResponse
from test_image_transport import PagedFiles,FakeFiles,payload,transport
from test_image_provider import decode_file

class GhostOnError(PagedFiles):
    def __init__(self,status):super().__init__();self.status=status
    _error=staticmethod(OpenRouterFilesBackend._error)
    async def upload(self,key,data,mime,filename):
        return await OpenRouterFilesBackend.upload(self,key,data,mime,filename)
    async def _call(self,method,suffix,key,**kwargs):
        assert method=='POST' and not suffix
        data,name,mime=decode_file(kwargs['data'],kwargs['content_type'])
        await FakeFiles.upload(self,key,data,mime,name)
        return _FileResponse(self.status,None,None,{'retry-after':'7'})

@pytest.mark.asyncio
@pytest.mark.parametrize('status',[400,401,403,413,415,422,429,500])
async def test_created_then_4xx_never_blindly_reuploads_and_can_reconcile(tmp_path,status):
    backend=GhostOnError(status);tr=transport(tmp_path,backend)
    with pytest.raises(FileOperationError) as error:
        async with tr.prepare(payload()):pass
    assert error.value.status==status
    assert error.value.safe_headers=={'retry-after':'7'}
    diag=await tr.diagnostics(private=True)
    # Opening a second instance simulates a cold local reader, not OS reboot.
    cold=transport(tmp_path,backend)
    with pytest.raises((UploadOutcomeUnknown, FileOperationError)) as attempted:
        async with cold.prepare(payload()):pass
    observed = {'http_status': status, 'definitely_not_created': error.value.definitely_not_created,
                'durable_states': [asset['state'] for asset in diag['assets']],
                'next_error': type(attempted.value).__name__, 'uploads': len(backend.uploaded),
                'remote_files': len(backend.files)}
    print('SYNTHETIC_UPLOAD_OBSERVATION ' + json.dumps(observed, sort_keys=True))
    assert not error.value.definitely_not_created and observed['durable_states']==['upload_unknown']
    assert isinstance(attempted.value, UploadOutcomeUnknown)
    assert len(backend.files)==1 and len(backend.uploaded)==1
    before=await cold.cleanup(all_unpinned=True)
    assert not before['complete'] and not backend.deleted and before['unknown_uploads']==1
    # Fully enumerated, unique, same-nonce metadata may be recovered only for cleanup.
    reconciled=await cold.reconcile_unknown_uploads()
    assert reconciled['complete'] and reconciled['resolved_count']==1
    assert len(backend.uploaded)==1 and len(backend.deleted)==1 and not backend.files

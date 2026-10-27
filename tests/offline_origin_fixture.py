"""Synthetic import-origin receipts for acceptance validator negative controls."""
import hashlib,json
from pathlib import Path,PureWindowsPath


def write_origins(root,metadata,native):
    sources=metadata.get('adapter_source_sha256')
    if not sources:
        metadata['adapter_source_sha256']={'src/codex_deepseek/__init__.py':'synthetic-product-hash'}
    cls=PureWindowsPath if native['target']=='x86_64-pc-windows-msvc' else Path
    prefix=cls(metadata['release_environment'])
    directory=prefix/'Lib/site-packages/codex_deepseek' if native['target']=='x86_64-pc-windows-msvc' else prefix/'lib/python3.13/site-packages/codex_deepseek'
    value={'schema_version':1,'isolated':1,'checks':2,'passed':True,'errors':[],
           'expected_product_directory':str(directory),'origins':{'codex_deepseek':str(directory/'__init__.py')},
           'adapter_identity':{'directory':str(directory),'source_sha256':{
               name.removeprefix('src/codex_deepseek/'):digest for name,digest in metadata['adapter_source_sha256'].items()}}}
    path=Path(root)/'offline-installed-origins.json';path.write_text(json.dumps(value),encoding='utf-8')
    return hashlib.sha256(path.read_bytes()).hexdigest()

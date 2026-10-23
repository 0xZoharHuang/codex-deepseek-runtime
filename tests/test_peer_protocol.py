# Strict Peer stdout evidence, with fake app-server processes only.
import io
import json
import queue
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace
import pytest
from verify_live import Peer


class FakeProcess:
    def __init__(self, raw, *, text=False, timeout=False):
        self.stdout = io.StringIO(raw.decode()) if text else io.BytesIO(raw)
        self.stdin = io.StringIO()
        self.actions = []
        self.returncode = None
        self.timeout = timeout
    def poll(self):
        return self.returncode
    def terminate(self):
        self.actions.append('terminate')
    def kill(self):
        self.actions.append('kill')
        self.returncode = -1
    def wait(self, timeout=None):
        self.actions.append('wait')
        if self.timeout and self.returncode is None:
            raise subprocess.TimeoutExpired('fake', timeout)
        self.returncode = 0 if self.returncode is None else self.returncode
        return self.returncode


def make_peer(tmp_path, raw, *, text=False, timeout=False):
    value = Peer.__new__(Peer)
    value.root = tmp_path
    value.events = []
    value.queue = queue.Queue()
    value.condition = threading.Condition()
    value.exited = False
    value.callbacks = []
    value.dynamic_answers = {}
    value.closed = False
    value.protocol_error = None
    value.protocol_evidence = tmp_path / 'private-protocol-fixture'
    value.process = FakeProcess(raw, text=text, timeout=timeout)
    value.stderr = io.StringIO()
    value.reader = threading.Thread(target=value.read, daemon=True)
    value.reader.start()
    value.reader.join(2)
    assert not value.reader.is_alive()
    return value


@pytest.mark.parametrize('bad', [b'not json\r\n', b'[]\n', b'false\n', b'{"result":{}}\n', b'\xff\n',
    b'{"id":1,"result":{"ok":false},"result":{"ok":true}}\n',
    b'{"id":1,"result":{"ok":false,"ok":true}}\n',
    b'{"id":1,"result":NaN}\n', b'{"id":1,"result":Infinity}\n', b'{"id":1,"result":-Infinity}\n'])
def test_pollution_before_valid_response_is_fatal_and_preserved(tmp_path, bad):
    value = make_peer(tmp_path, bad + b'{"id":1,"result":{"ok":true}}\n')
    with pytest.raises(RuntimeError, match='protocol'):
        value.wait(lambda item: item.get('id') == 1, timeout=.1)
    assert value.protocol_error
    assert Path(value.protocol_error['raw_path']).read_bytes() == bad
    with pytest.raises(RuntimeError, match='protocol'):
        value.close()
    assert value.process.actions == ['terminate', 'wait']
    assert value.stderr.closed and value.process.stdin.closed and value.process.stdout.closed
    assert len(list(tmp_path.glob('events-*.json'))) == 1
    with pytest.raises(RuntimeError, match='protocol'):
        value.close()


@pytest.mark.parametrize('text', [False, True])
def test_blank_lines_and_valid_events_keep_normal_contract(tmp_path, text):
    value = make_peer(tmp_path, b' \t\r\n{"method":"notice"}\n{"id":1,"result":{}}\n', text=text)
    assert value.wait(lambda event: event.get('id') == 1, timeout=.1)['result'] == {}
    value.close()
    assert value.protocol_error is None
    assert not value.protocol_evidence.exists()
    recorded = json.loads(next(tmp_path.glob('events-*.json')).read_text())
    assert recorded == [{'method':'notice'}, {'id':1,'result':{}}]


def test_late_pollution_cannot_hide_behind_cached_valid_response(tmp_path):
    value = make_peer(tmp_path, b'{"id":1,"result":{}}\nnoise\n', timeout=True)
    with pytest.raises(RuntimeError, match='protocol'):
        value.wait(lambda event: event.get('id') == 1, timeout=.1)
    with pytest.raises(RuntimeError, match='protocol'):
        value.close()
    assert value.process.actions == ['terminate', 'wait', 'kill', 'wait']
    assert value.stderr.closed and value.process.stdin.closed and value.process.stdout.closed


def test_reader_uses_raw_buffer_without_losing_invalid_utf8(tmp_path):
    value = Peer.__new__(Peer)
    value.root = tmp_path
    value.events=[];value.queue=queue.Queue();value.condition=threading.Condition();value.exited=False
    value.callbacks=[];value.dynamic_answers={};value.protocol_error=None
    value.protocol_evidence=tmp_path/'raw-wrapper'
    wrapped=io.TextIOWrapper(io.BytesIO(b'\xff\r\n'),encoding='utf-8')
    value.process=SimpleNamespace(stdout=wrapped)
    value.read()
    assert value.exited and value.protocol_error
    assert Path(value.protocol_error['raw_path']).read_bytes() == b'\xff\r\n'
    wrapped.close()


class QueuedStdout:
    def __init__(self):
        self.lines = queue.Queue()
        self.closed = False
    def __iter__(self):
        return self
    def __next__(self):
        line = self.lines.get(timeout=2)
        if line is None:raise StopIteration
        return line
    def close(self):
        self.closed = True


def test_protocol_error_wakes_already_pending_waiter(tmp_path):
    value = make_peer(tmp_path, b'')
    value.exited = False
    value.queue = queue.Queue()
    value.process.stdout = QueuedStdout()
    value.reader = threading.Thread(target=value.read, daemon=True)
    value.reader.start()
    started = threading.Event()
    errors = []
    def wait_for_response():
        started.set()
        try:value.wait(lambda event: event.get('id') == 1, timeout=30)
        except Exception as error:errors.append(error)
    waiter = threading.Thread(target=wait_for_response, daemon=True)
    waiter.start()
    assert started.wait(1)
    value.process.stdout.lines.put(b'pollution\n')
    waiter.join(2)
    assert not waiter.is_alive() and len(errors) == 1
    assert isinstance(errors[0], RuntimeError) and 'protocol' in str(errors[0])
    with pytest.raises(RuntimeError, match='protocol'):
        value.close()
    assert not value.reader.is_alive() and value.process.stdout.closed


def test_one_handle_close_failure_does_not_skip_other_cleanup(tmp_path):
    value = make_peer(tmp_path, b'pollution\n')
    class FailedClose:
        def close(self):
            raise OSError('fixture stdin close failure')
    value.process.stdin = FailedClose()
    with pytest.raises(RuntimeError, match='protocol'):
        value.close()
    assert value.process.actions == ['terminate','wait']
    assert value.process.stdout.closed and value.stderr.closed
    assert not value.reader.is_alive()
    assert value.close_errors == [('stdin_close','fixture stdin close failure')]
    assert len(list(tmp_path.glob('events-*.json'))) == 1

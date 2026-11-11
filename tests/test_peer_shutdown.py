"""QA shutdown observations using isolated stdio children, never the native/model runtime."""
import contextlib
import json
import os
import queue
import subprocess
import sys
import threading
import time

import pytest

from codex_deepseek.processes import identity, matches
from verify_live import Peer


@contextlib.contextmanager
def stdio_peer(root, *, exit_code=0, ignore_eof=False):
    marker=root/"received-eof"
    behavior="time.sleep(60)" if ignore_eof else (
        "sys.stdin.read(); pathlib.Path("+repr(str(marker))+").write_text('EOF'); "
        "print(json.dumps({'method':'fixture/eof'}),flush=True); raise SystemExit("+str(exit_code)+")")
    script="import json,pathlib,sys,time; print(json.dumps({'method':'fixture/ready'}),flush=True); "+behavior
    # The physical interpreter avoids a venv launcher child surviving a fallback.
    process=subprocess.Popen([sys._base_executable,"-I","-B","-u","-c",script],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        text=True,encoding="utf-8",bufsize=1,
        env={k:v for k,v in os.environ.items() if k.upper() in {"SYSTEMROOT","WINDIR","TEMP","TMP"}})
    birth=identity(process.pid)
    assert birth and matches(process.pid,birth)
    peer=Peer.__new__(Peer)
    peer.root=root;peer.events=[];peer.queue=queue.Queue();peer.condition=threading.Condition()
    peer.exited=False;peer.callbacks=[];peer.dynamic_answers={};peer.closed=False;peer.protocol_error=None
    peer.protocol_evidence=root/"protocol-error";peer.process=process;peer.stderr=process.stderr
    peer.process_identity={"pid":process.pid,"birth":birth,"ownership":"created_Popen_handle"}
    peer.reader=threading.Thread(target=peer.read,daemon=True);peer.reader.start()
    try:
        peer.wait(lambda event:event.get("method")=="fixture/ready",timeout=3)
        yield peer,marker
    finally:
        if process.poll() is None:
            assert matches(process.pid,birth)
            process.kill();process.wait(timeout=3)
        peer.reader.join(3)
        for stream in [process.stdin,process.stdout,process.stderr]:
            if not stream.closed:stream.close()


def shutdown_receipt(peer):
    files=list(peer.root.glob("shutdown-*.json"))
    assert len(files)==1
    receipt=json.loads(files[0].read_text(encoding="utf-8"))
    assert receipt["process"]==peer.process_identity
    assert not matches(receipt["process"]["pid"],receipt["process"]["birth"])
    return receipt


@pytest.mark.parametrize("exit_code",[0,7])
def test_eof_result_is_observed_without_signalling(tmp_path,exit_code):
    with stdio_peer(tmp_path,exit_code=exit_code) as (peer,marker):
        if exit_code:
            with pytest.raises(RuntimeError,match="eof_exit"):peer.close(timeout=2)
        else:peer.close(timeout=2)
        receipt=shutdown_receipt(peer)
        assert marker.read_text()=="EOF"
        assert any(event.get("method")=="fixture/eof" for event in peer.events)
        assert receipt["phase"]=="eof" and receipt["forced"]==[]
        assert receipt["returncode"]==exit_code
        assert receipt["natural_exit"] is (exit_code==0)
        assert receipt["outcome"]==("graceful" if exit_code==0 else "failed")
        if not exit_code:
            peer.close()
            assert shutdown_receipt(peer)==receipt


def test_preexisting_nonzero_exit_is_already_stopped_not_natural(tmp_path):
    with stdio_peer(tmp_path,exit_code=42) as (peer,_):
        peer.process.stdin.close();peer.process.wait(timeout=2)
        peer.close(timeout=2)
        receipt=shutdown_receipt(peer)
        assert receipt["outcome"]==receipt["phase"]=="already_stopped"
        assert receipt["returncode"]==42 and receipt["forced"]==[]
        assert receipt["natural_exit"] is False and receipt["clean_exit"] is False


def test_broken_stdin_is_reported_even_when_eof_retires_child(tmp_path):
    with stdio_peer(tmp_path) as (peer,marker):
        real=peer.process.stdin
        class BrokenClose:
            def close(self):
                real.close()
                raise BrokenPipeError("fixture pipe close failed")
        peer.process.stdin=BrokenClose()
        with pytest.raises(RuntimeError,match="stdin_close"):peer.close(timeout=2)
        receipt=shutdown_receipt(peer)
        assert marker.exists() and receipt["returncode"]==0 and receipt["forced"]==[]
        assert receipt["stdin"]["type"]=="BrokenPipeError"
        assert receipt["outcome"]=="failed" and receipt["natural_exit"] is False
        peer.process.stdin=real


@pytest.mark.parametrize("ignore_terminate",[False,True])
def test_eof_timeout_fallback_is_bounded_and_fails_acceptance(tmp_path,ignore_terminate):
    with stdio_peer(tmp_path,ignore_eof=True) as (peer,marker):
        if ignore_terminate:
            real=peer.process
            class IgnoreTerminate:
                def __getattr__(self,name):return getattr(real,name)
                def terminate(self):pass
            peer.process=IgnoreTerminate()
        started=time.monotonic()
        with pytest.raises(RuntimeError,match="forced_shutdown"):peer.close(timeout=.1)
        receipt=shutdown_receipt(peer)
        assert time.monotonic()-started<3 and not marker.exists()
        assert receipt["phase"]==("kill" if ignore_terminate else "terminate")
        assert receipt["forced"][0]=={"phase":"terminate","reason":"eof_timeout"}
        if ignore_terminate:assert receipt["forced"][1]=={"phase":"kill","reason":"terminate_timeout"}
        assert receipt["outcome"]=="failed" and receipt["natural_exit"] is False


def test_explicit_fault_close_is_intentional_and_does_not_wait_for_eof(tmp_path):
    with stdio_peer(tmp_path) as (peer,marker):
        peer.close(force=True,timeout=2)
        receipt=shutdown_receipt(peer)
        assert not marker.exists()
        assert receipt["outcome"]=="intentional" and receipt["requested"]=="force"
        assert receipt["forced"]==[{"phase":"terminate","reason":"explicit_force"}]
        assert receipt["natural_exit"] is False and receipt["errors"]==[]

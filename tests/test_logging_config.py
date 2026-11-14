import logging
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest

from backend import logging_config


def test_logging_setup_quiets_http_clients(monkeypatch):
    httpx = logging.getLogger("httpx")
    httpcore = logging.getLogger("httpcore")
    monkeypatch.setattr(httpx, "level", logging.NOTSET)
    monkeypatch.setattr(httpcore, "level", logging.NOTSET)

    logging_config.configure_logging()

    assert httpx.level == logging.WARNING
    assert httpcore.level == logging.WARNING


def test_exception_log_has_traceback_without_exception_message(caplog):
    logger = logging.getLogger("test.safe-logging")
    secret_body = "private-provider-response"
    try:
        raise ValueError(secret_body)
    except ValueError as error:
        logging_config.log_exception(logger, "Request failed", error)

    assert "Request failed (ValueError)" in caplog.text
    assert "test_exception_log_has_traceback_without_exception_message" in caplog.text
    assert secret_body not in caplog.text


@pytest.mark.parametrize('rootless', [False, True])
def test_configuration_upgrade_preserves_credentials_and_uses_rootless_fallback(tmp_path, monkeypatch, capsys, rootless):
    script = tmp_path / 'scripts' / 'configure.py'
    script.parent.mkdir()
    script.write_text((Path(__file__).parents[1] / 'scripts' / 'configure.py').read_text())
    private = tmp_path / '.env'
    private.write_text('OWNER_TOKEN=private-test-token\nCORE_OOM_SCORE_ADJ=-500\nDATA_OOM_SCORE_ADJ=-997\n')
    monkeypatch.setattr('subprocess.run', lambda *a, **kw: SimpleNamespace(stdout='["name=rootless"]' if rootless else '[]'))
    with pytest.raises(SystemExit) as exit:
        runpy.run_path(str(script))
    assert exit.value.code == 0
    pairs = dict(line.split('=', 1) for line in private.read_text().splitlines() if '=' in line)
    assert pairs['OWNER_TOKEN'] == 'private-test-token' and pairs['BOT_OOM_SCORE_ADJ'] == '1000'
    assert pairs['CORE_OOM_SCORE_ADJ'] == ('0' if rootless else '-500')
    assert pairs['DATA_OOM_SCORE_ADJ'] == ('0' if rootless else '-997')
    assert 'private-test-token' not in capsys.readouterr().out

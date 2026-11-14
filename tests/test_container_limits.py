"""Supervisor resource policy checks without a database or a Docker connection."""

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
import httpx

from backend import containers, limits


MAC = {"cpus": 4, "memory_bytes": 8_320_299_008, "pid_max": 99_999,
       "threads_max": 63_765, "nr_open": 1_048_576, "logging_driver": "json-file"}
FROZEN = {"pids_limit": 2048, "nofile_hard": 32768, "allow_swap": False,
          "log_size_mb": 25, "log_files": 7}
VOLUMES = {"data": "test-data", "workspace": "test-workspace", "browser": "test-browser"}


def spec(**changes):
    return containers.RuntimeSpec(**{
        "bot_id": "bot-test", "operation_id": "00000000-0000-0000-0000-000000000001",
        "revision": 1, "configuration": {"name": "Test bot", "role": "Test resource policy", "memory_mb": 2048},
        "model_base_url": "https://example.invalid/v1", "model_api_key": "test-key",
        "runtime_key": "r" * 32, "adapter_key": "a" * 32, "bot_token": "b" * 32,
        **changes,
    })


class Docker:
    def __init__(self):
        self.calls, self.created = [], {}
        self.wait_status = 0

    def __call__(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if path == "/info":
            return {"NCPU": 4, "MemTotal": MAC["memory_bytes"], "OperatingSystem": "Docker Desktop",
                    "CgroupVersion": "2", "MemoryLimit": True, "PidsLimit": True, "SwapLimit": True,
                    "LoggingDriver": "json-file", "SecurityOptions": ["name=rootless"]}
        if path.startswith("/images/"):
            return {"Id": "sha256:test-image"}
        if path == "/containers/create":
            body = kwargs["json"]
            name = body["Labels"]["com.hermbot.kind"]
            self.created[name] = body
            return {"Id": name}
        if path.endswith("/wait"):
            return {"StatusCode": self.wait_status}
        if path.endswith("/json"):
            name = path.split("/")[2]
            return {"HostConfig": self.created.get(name, {}).get("HostConfig", {}),
                    "State": {"StartedAt": "2026-11-14T00:00:00Z", "Running": True}}
        return {}


class ContainerLimitTests(unittest.TestCase):
    def setUp(self):
        self.docker = Docker()
        for name, value in (
            ("docker_request", self.docker), ("read_machine", lambda: MAC),
            ("ensure_resources", lambda bot_id: ("test-network", VOLUMES)),
            ("find_runtime", lambda *a, **kw: None), ("remove_container", lambda value: None),
            ("runtime_ready", lambda container: None),
        ):
            self.enterContext(patch.object(containers, name, value))
        self.enterContext(patch.dict(containers.FENCED, {}, clear=True))
        self.enterContext(patch.dict(containers.MACHINE_CACHE, {}, clear=True))

    def test_frozen_limits_reach_runtime_hostconfig_and_observation(self):
        result = containers.ensure(spec(limits=FROZEN))
        host = self.docker.created["runtime"]["HostConfig"]
        self.assertEqual(host["Memory"], 2048 * limits.MIB)
        self.assertEqual(host["NanoCpus"], 1_500_000_000)
        self.assertEqual(host["PidsLimit"], 2048)
        self.assertEqual(host["MemorySwap"], host["Memory"])
        self.assertEqual(host["Ulimits"], [{"Name": "nofile", "Soft": 1024, "Hard": 32768}])
        self.assertEqual(host["OomScoreAdj"], limits.BOT_OOM_SCORE_ADJ)
        self.assertEqual(host["LogConfig"], {"Type": "json-file", "Config": {"max-size": "25m", "max-file": "7"}})
        self.assertEqual(result["limits_observed"]["PidsLimit"], host["PidsLimit"])
        self.assertEqual(result["limits_observed"]["MemorySwap"], host["Memory"])
        self.assertTrue(host["ReadonlyRootfs"])
        self.assertEqual(host["CapDrop"], ["ALL"])

    def test_swap_opt_in_adds_only_one_quarter_of_memory(self):
        host = containers.resource_host(spec(limits={**FROZEN, "allow_swap": True}))
        self.assertEqual(host["MemorySwap"], 2560 * limits.MIB)

    def test_observation_reports_docker_values_even_when_they_differ(self):
        with patch.object(containers, "docker_request", return_value={"HostConfig": {"PidsLimit": 128}}):
            result = containers.inspect_limits("runtime", spec(limits=FROZEN))
        self.assertEqual(result["limits_observed"]["PidsLimit"], 128)
        self.assertIsNone(result["limits_observed"]["MemorySwap"])

    def test_local_logs_rotate_and_unsupported_driver_is_preserved(self):
        for driver in ("local", "syslog", "journald", ""):
            with self.subTest(driver=driver), patch.object(containers, "read_machine", return_value={"logging_driver": driver}):
                host = containers.resource_host(spec(limits=FROZEN))
                if driver == "local":
                    self.assertEqual(host["LogConfig"]["Type"], "local")
                    self.assertEqual(host["LogConfig"]["Config"]["max-file"], "7")
                else:
                    self.assertNotIn("LogConfig", host)

    def test_legacy_operation_has_no_new_runtime_limits_or_machine_dependency(self):
        with patch.object(containers, "read_machine", side_effect=AssertionError("Legacy replay must not probe")):
            result = containers.ensure(spec())
        host = self.docker.created["runtime"]["HostConfig"]
        self.assertNotIn("limits_observed", result)
        for key in ("PidsLimit", "MemorySwap", "Ulimits", "LogConfig", "OomScoreAdj"):
            self.assertNotIn(key, host)

    def test_runtime_limit_schema_rejects_coercions_and_unknown_fields(self):
        for changes in ({"pids_limit": True}, {"pids_limit": "512"}, {"pids_limit": 127},
                        {"nofile_hard": 1023}, {"allow_swap": 0}, {"log_size_mb": 101},
                        {"log_files": 0}, {"log_files": 5.0}, {"unknown": 1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                spec(limits={**FROZEN, **changes})

    def test_probe_failure_preserves_existing_runtime(self):
        old = {"Id": "old-runtime", "State": "running", "Labels": {}}
        with patch.object(containers, "find_runtime", return_value=old), \
                patch.object(containers, "remove_container") as remove, \
                patch.object(containers, "read_machine", side_effect=HTTPException(503, "Unavailable")):
            with self.assertRaises(HTTPException):
                containers.ensure(spec(limits=FROZEN))
            remove.assert_not_called()
        self.assertNotIn("runtime", self.docker.created)

    def test_lower_kernel_file_ceiling_does_not_remove_the_existing_runtime(self):
        with patch.object(containers, 'read_machine', return_value={**MAC, 'nr_open': 16384}), \
                patch.object(containers, 'remove_container') as remove:
            with self.assertRaises(HTTPException) as error:
                containers.ensure(spec(limits=FROZEN))
            self.assertEqual(error.exception.status_code, 409)
            remove.assert_not_called()

    def test_fence_preserves_network_privileges_and_cleans_up_on_failure(self):
        for status in (0, 1):
            self.docker.wait_status = status
            if status:
                with self.assertRaises(HTTPException):
                    containers.fence("runtime", "bot-test")
            else:
                containers.fence("runtime", "bot-test")
            host = self.docker.created["fence-helper"]["HostConfig"]
            self.assertEqual(host["NetworkMode"], "container:runtime")
            self.assertEqual(set(host["CapAdd"]), {"NET_ADMIN", "NET_RAW"})
            self.assertEqual(host["Memory"], 64 * limits.MIB)
            self.assertEqual(host["MemorySwap"], host["Memory"])
            self.assertEqual(host["PidsLimit"], limits.HELPER_PIDS_LIMIT)
            self.assertEqual(host["OomScoreAdj"], limits.BOT_OOM_SCORE_ADJ)
            self.assertTrue(host["ReadonlyRootfs"])
            self.assertTrue(any(method == "DELETE" and path == "/containers/fence-helper"
                                for method, path, kwargs in self.docker.calls))

    def test_clone_helper_keeps_source_readonly_and_has_bounded_resources(self):
        with patch.object(containers, "optional_get", side_effect=lambda path: {"Name": "source"} if path.startswith("/volumes/") else None), \
                patch.object(containers, "owns_resource", return_value=True):
            containers.clone_state(spec(source_bot_id="source-bot", copy_options={"workspace": True}), VOLUMES)
        body = self.docker.created["clone-helper"]
        host = body["HostConfig"]
        self.assertEqual(host["NetworkMode"], "none")
        self.assertEqual(host["Memory"], 512 * limits.MIB)
        self.assertEqual(host["MemorySwap"], host["Memory"])
        self.assertEqual(host["PidsLimit"], limits.HELPER_PIDS_LIMIT)
        self.assertEqual(host["OomScoreAdj"], limits.BOT_OOM_SCORE_ADJ)
        self.assertNotIn("Privileged", host)
        self.assertTrue(all(bind.endswith(":ro") for bind in host["Binds"][:2]))
        self.assertEqual(body["Entrypoint"][:2], containers.RUNTIME_PYTHON)

    def test_owner_private_session_retains_isolation_and_gains_limits(self):
        with patch.object(containers, "remove_owner_session"), \
                patch.object(containers, "filter_containers", return_value=[{"Id": "api-container"}]):
            result = containers.start_owner_session("bot-test", containers.OwnerSessionSpec())
        body = self.docker.created["owner-session"]
        host = body["HostConfig"]
        self.assertEqual(body["User"], "10001:10001")
        self.assertEqual(host["NetworkMode"], "hermbot-owner-bot-test")
        self.assertEqual(host["CapDrop"], ["ALL"])
        self.assertNotIn("CapAdd", host)
        self.assertNotIn("Binds", host)
        self.assertEqual(host["MemorySwap"], host["Memory"])
        self.assertEqual(host["PidsLimit"], 512)
        self.assertEqual(host["Ulimits"][0]["Soft"], limits.NOFILE_SOFT)
        self.assertEqual(host["OomScoreAdj"], limits.BOT_OOM_SCORE_ADJ)
        self.assertEqual(result["url"], "http://hermbot-owner-bot-test:8650")
        self.assertEqual(len(result["token"]), 43)
        self.assertTrue(any(path.endswith("/connect") and kwargs["json"]["Container"] == "api-container"
                            for method, path, kwargs in self.docker.calls))

    def test_owner_and_helper_logs_preserve_unsupported_driver(self):
        with patch.dict(containers.MACHINE_CACHE, {"facts": {"logging_driver": "syslog"}}, clear=True):
            self.assertNotIn("LogConfig", containers.helper_limits(32))
        with patch.object(containers, "read_machine", return_value={"logging_driver": "syslog"}), \
                patch.object(containers, "remove_owner_session"), \
                patch.object(containers, "filter_containers", return_value=[]):
            containers.start_owner_session("bot-test", containers.OwnerSessionSpec())
        self.assertNotIn("LogConfig", self.docker.created["owner-session"]["HostConfig"])

    def test_private_browser_file_default_follows_the_kernel_ceiling(self):
        with patch.object(containers, 'read_machine', return_value={**MAC, 'nr_open': 8192}), \
                patch.object(containers, 'remove_owner_session'), \
                patch.object(containers, 'filter_containers', return_value=[]):
            containers.start_owner_session('bot-test', containers.OwnerSessionSpec())
        self.assertEqual(self.docker.created['owner-session']['HostConfig']['Ulimits'][0]['Hard'], 8192)
        with patch.object(containers, 'read_machine', return_value={**MAC, 'nr_open': 512}), \
                patch.object(containers, 'remove_owner_session') as remove:
            with self.assertRaises(HTTPException):
                containers.start_owner_session('bot-test', containers.OwnerSessionSpec())
            remove.assert_not_called()

    def test_preupgrade_receipt_replays_without_execution(self):
        request = spec()
        legacy = {
            "bot_id": "bot-test", "operation_id": request.operation_id, "revision": 1,
            "configuration": {"name": "Test bot", "role": "Test resource policy", "color": "#829c77",
                              "avatar": "", "provider_id": None, "model_name": "", "capabilities": [],
                              "enabled_tools": ["file", "terminal"], "enabled_skills": [],
                              "memory_mb": 2048, "cpus": 1.5},
            "model_base_url": "https://example.invalid/v1", "model_api_key": "test-key",
            "model_auth_kind": "api_key", "model_provider_url": "", "runtime_key": "r" * 32,
            "adapter_key": "a" * 32, "bot_token": "b" * 32, "timezone": "UTC", "desired_state": "running",
            "source_bot_id": None, "source_was_running": False, "copy_options": {},
        }
        digest = hashlib.sha256(json.dumps({"action": "ensure", "body": legacy,
                                            "image": containers.RUNTIME_IMAGE}, sort_keys=True).encode()).hexdigest()
        self.assertEqual(containers.operation_body(request), legacy)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / (request.operation_id + ".json")).write_text(json.dumps({
                "request_hash": digest, "result": {"container_id": "legacy-runtime", "revision": 1}}))
            with patch.object(containers, "Path", return_value=root), \
                    patch.object(containers, "execute_operation") as execute, \
                    patch.object(containers, "cleanup_clone"):
                self.assertEqual(containers.operate("ensure", request)["container_id"], "legacy-runtime")
                execute.assert_not_called()
                with self.assertRaises(HTTPException) as error:
                    containers.operate("ensure", spec(limits=FROZEN))
                self.assertEqual(error.exception.status_code, 409)


class MachineFactTests(unittest.TestCase):
    def setUp(self):
        self.docker = Docker()
        self.enterContext(patch.object(containers, "docker_request", self.docker))
        self.enterContext(patch.dict(containers.MACHINE_CACHE, {}, clear=True))
        readings = {"/proc/sys/kernel/threads-max": "63765\n", "/proc/sys/fs/file-max": "811881\n",
                    "/proc/sys/fs/nr_open": "1048576\n"}
        self.enterContext(patch.object(Path, "read_text", lambda path, *a, **kw: readings[str(path)]))
        self.enterContext(patch.object(Path, "exists", return_value=True))
        self.enterContext(patch.object(containers.os, "statvfs", return_value=SimpleNamespace(
            f_blocks=911, f_frsize=limits.GIB, f_bavail=827, f_files=100, f_favail=99)))
        self.client = MagicMock()
        self.client.__enter__.return_value.get.return_value = httpx.Response(
            200, text="99999\n", request=httpx.Request("GET", "http://docker/test"))
        self.enterContext(patch.object(containers.httpx, "Client", return_value=self.client))

    def test_host_pid_helper_is_isolated_bounded_and_always_removed(self):
        facts = containers.read_machine()
        self.assertEqual(facts["pid_max"], 99999)
        self.assertEqual(facts["threads_max"], 63765)
        self.assertEqual(facts["nr_open"], 1048576)
        self.assertTrue(facts["rootless"])
        self.assertEqual(facts["disk_free_bytes"], 827 * limits.GIB)
        self.assertTrue(any("virtual machine" in warning for warning in facts["warnings"]))
        host = self.docker.created["machine-helper"]["HostConfig"]
        self.assertEqual(host["PidMode"], "host")
        self.assertEqual(host["NetworkMode"], "none")
        self.assertTrue(host["ReadonlyRootfs"])
        self.assertEqual(host["CapDrop"], ["ALL"])
        for key in ("Privileged", "CapAdd", "Binds"):
            self.assertNotIn(key, host)
        self.assertEqual(host["SecurityOpt"], ["no-new-privileges:true"])
        self.assertEqual(host["Memory"], 32 * limits.MIB)
        self.assertEqual(host["MemorySwap"], host["Memory"])
        self.assertEqual(host["PidsLimit"], limits.HELPER_PIDS_LIMIT)
        self.assertIn(("DELETE", "/containers/machine-helper"), [call[:2] for call in self.docker.calls])

    def test_pid_helper_failure_keeps_thread_fallback_and_cleanup(self):
        self.docker.wait_status = 1
        facts = containers.read_machine()
        self.assertIsNone(facts["pid_max"])
        self.assertEqual(facts["threads_max"], 63765)
        self.assertEqual(limits.resolved_values(facts)["pids_limit"], 1024)
        self.assertIn(("DELETE", "/containers/machine-helper"), [call[:2] for call in self.docker.calls])

    def test_pid_helper_start_failure_still_removes_created_container(self):
        def fail_start(method, path, **kwargs):
            if path.endswith("/start"):
                raise HTTPException(500, "Start failed")
            return self.docker(method, path, **kwargs)
        with patch.object(containers, "docker_request", side_effect=fail_start):
            facts = containers.read_machine()
        self.assertIsNone(facts["pid_max"])
        self.assertIn(("DELETE", "/containers/machine-helper"), [call[:2] for call in self.docker.calls])

    def test_cleanup_failure_is_reported_without_discarding_machine_facts(self):
        def fail_cleanup(method, path, **kwargs):
            if method == "DELETE":
                raise HTTPException(500, "Cleanup failed")
            return self.docker(method, path, **kwargs)
        with patch.object(containers, "docker_request", side_effect=fail_cleanup):
            facts = containers.read_machine()
        self.assertEqual(facts["pid_max"], 99999)
        self.assertTrue(any("cleanup" in warning for warning in facts["warnings"]))

    def test_missing_proc_and_disk_readings_are_explicit(self):
        with patch.object(Path, "read_text", side_effect=OSError("unavailable")), \
                patch.object(containers.os, "statvfs", side_effect=OSError("unavailable")):
            facts = containers.read_machine()
        self.assertIsNone(facts["threads_max"])
        self.assertIsNone(facts["nr_open"])
        self.assertTrue(any("filesystem" in warning for warning in facts["warnings"]))

    def test_machine_facts_cache_expires(self):
        with patch.object(containers.time, "monotonic", return_value=100):
            first = containers.read_machine()
        with patch.object(containers.time, "monotonic", return_value=159):
            self.assertIs(containers.read_machine(), first)
        self.assertEqual(sum(path == "/info" for method, path, kwargs in self.docker.calls), 1)
        with patch.object(containers.time, "monotonic", return_value=161):
            self.assertIsNot(containers.read_machine(), first)
        self.assertEqual(sum(path == "/info" for method, path, kwargs in self.docker.calls), 2)

    def test_machine_route_rejects_unauthenticated_requests(self):
        with patch.object(containers, "settings", SimpleNamespace(supervisor_token="test-supervisor-token")), \
                patch.object(containers, "read_machine", return_value=MAC) as read, TestClient(containers.app) as client:
            self.assertEqual(client.get("/machine").status_code, 401)
            read.assert_not_called()
            response = client.get("/machine", headers={"Authorization": "Bearer test-supervisor-token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["memory_bytes"], MAC["memory_bytes"])


if __name__ == "__main__":
    unittest.main()

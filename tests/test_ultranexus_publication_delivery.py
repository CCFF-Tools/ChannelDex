import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import TestCase, override_settings
from django.db import IntegrityError, transaction

from pubtv.operations.automation import canonical_hash, publication_snapshot, settings_record_snapshot
from pubtv.operations.models import (ActivationEvidence, ArtifactRevision, Device, Occurrence,
    ResearchGate, ScheduleDeliveryOperation, SchedulePublicationBatch, Station,
    UltraNexusTargetSettings, UploadedOccurrenceCoverage)
from pubtv.operations.publication_delivery import (DELIVERY_GATES, activate_publication,
    observe_activation, rollback_publication, stage_publication)
from tests.test_ultranexus_bin import fixture


class FakeFTPConnection:
    files = {}

    def connect(self, host, port, timeout):
        self.host, self.port = host, port

    def login(self, username, password):
        pass

    def set_pasv(self, enabled):
        pass

    def size(self, remote):
        if remote not in self.files:
            raise FileNotFoundError(remote)
        return len(self.files[remote])

    def storbinary(self, command, source, blocksize):
        self.files[command.removeprefix("STOR ")] = source.read()

    def retrbinary(self, command, callback, blocksize):
        remote = command.removeprefix("RETR ")
        if remote not in self.files:
            raise FileNotFoundError(remote)
        data = self.files[remote]
        for index in range(0, len(data), blocksize):
            callback(data[index:index + blocksize])

    def rename(self, old, new):
        if new in self.files:
            raise FileExistsError(new)
        self.files[new] = self.files.pop(old)

    def nlst(self, parent):
        return [path for path in self.files if path.startswith(parent.rstrip("/") + "/")]

    def delete(self, path):
        self.files.pop(path)

    def quit(self):
        pass


class FakeFTPAdapter:
    def __init__(self, host, *, port, username, password):
        self.host = host

    def _connect(self):
        return FakeFTPConnection()

    def download(self, remote, local):
        Path(local).write_bytes(FakeFTPConnection.files[remote])


class FailedPromotionConnection(FakeFTPConnection):
    def rename(self, old, new):
        if old.endswith(".part"):
            raise OSError("simulated promotion failure")
        super().rename(old, new)


class FailedPromotionAdapter(FakeFTPAdapter):
    def _connect(self):
        return FailedPromotionConnection()


class FailedUploadConnection(FakeFTPConnection):
    def storbinary(self, command, source, blocksize):
        raise OSError("simulated upload failure")


class FailedUploadAdapter(FakeFTPAdapter):
    def _connect(self):
        return FailedUploadConnection()


class FakeSocket:
    def __init__(self):
        self.responses = iter((b"331 User name OK\r\n", b"230 User Logged in\r\n", b"200 Command OK\r\n"))
        self.sent = []

    def settimeout(self, seconds):
        pass

    def sendall(self, data):
        self.sent.append(data)

    def recv(self, size):
        return next(self.responses, b"")

    def close(self):
        pass


class TimeoutAfterLoadSocket(FakeSocket):
    def recv(self, size):
        if any(line.startswith(b"LOADSCH ") for line in self.sent):
            raise TimeoutError("simulated lost acknowledgement")
        return super().recv(size)


class AuthenticationRejectedSocket(FakeSocket):
    def __init__(self):
        self.responses = iter((b"331 User name OK\r\n", b"530 Authentication rejected\r\n"))
        self.sent = []


class AttendedDeliveryTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.override = override_settings(DATA_DIR=self.directory.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        base = fixture()
        candidate = bytearray(base)
        candidate[1000] = 1
        from pubtv.ultranexus.bin import CHECKSUM_OFFSET, stored_checksum
        import struct
        struct.pack_into("<I", candidate, CHECKSUM_OFFSET, stored_checksum(candidate))
        self.base_path = Path(self.directory.name) / "base.bin"
        self.candidate_path = Path(self.directory.name) / "candidate.bin"
        self.nmg_path = Path(self.directory.name) / "review.nmg"
        self.base_path.write_bytes(base)
        self.candidate_path.write_bytes(candidate)
        from pubtv.ultranexus.nmg import MAGIC, VERSION_OFFSET
        nmg = bytearray(256)
        nmg[:8] = MAGIC
        nmg[VERSION_OFFSET:VERSION_OFFSET + 8] = b"7.0.3.48"
        self.nmg_path.write_bytes(nmg)
        self.base_hash = hashlib.sha256(base).hexdigest()
        self.candidate_hash = hashlib.sha256(candidate).hexdigest()
        self.nmg_hash = hashlib.sha256(nmg).hexdigest()
        FakeFTPConnection.files = {"/internal/schedule/schedule.bin": base}

        self.station = Station.objects.create()
        self.target = Device.objects.create(name="PUB-TV UltraNEXUS")
        self.occurrence = Occurrence.objects.create(
            station=self.station, item_type="filler", label="Clip",
            starts_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc), planned_duration_seconds=61,
        )
        self.settings = UltraNexusTargetSettings.objects.create(
            target=self.target, version=1, is_current=True, host="controller.test", port=21,
            media_directory="/Vol1/mpeg", schedule_path="/internal/schedule/schedule.bin",
            secret_reference="ftp:operator",
            command_username="operator", command_secret_reference="command:operator",
            base_nmg_path=str(self.nmg_path), base_nmg_hash=self.nmg_hash,
            base_bin_path=str(self.base_path), base_bin_hash=self.base_hash,
            controller_family="UltraNEXUS-HD", firmware_version="7.0.3.48",
            output_number=1, media_profile="Nexus Mono", qualification_status="passed",
            profile_identity_hash="1" * 64, nmg_template_sha256="2" * 64,
            bin_template_sha256="3" * 64, qualification_evidence_hash="4" * 64,
        )
        for gate in DELIVERY_GATES:
            ResearchGate.objects.create(target=self.target, key=gate, status="passed")
        ResearchGate.objects.create(target=self.target, key="resource_registration", status="passed")
        self.batch = SchedulePublicationBatch.objects.create(target=self.target)
        self.batch.occurrence_selections.create(occurrence=self.occurrence,
                                                 occurrence_revision=self.occurrence.revision)
        from pubtv.operations.publication_review import capture_controller_snapshot
        snapshot = capture_controller_snapshot(
            self.batch,
            adapter=FakeFTPAdapter("controller.test", port=21, username="test", password="test"),
        )
        item = {"occurrence_id": self.occurrence.pk, "occurrence_revision": self.occurrence.revision,
                "operation": "add"}
        mutation_plan = {"scope": "change_set", "controller_snapshot_hash": snapshot.snapshot_hash,
                         "items": [item], "affected_intervals": []}
        self.batch.mutation_plan = mutation_plan
        self.batch.mutation_plan_hash = canonical_hash(mutation_plan)
        self.batch.save(update_fields=["mutation_plan", "mutation_plan_hash"])
        ArtifactRevision.objects.create(
            publication_batch=self.batch, artifact_type="nmg", revision=1,
            file_reference=str(self.nmg_path), content_hash=self.nmg_hash,
            manifest={"items": [item], "target_id": self.target.pk,
                      "target_settings": settings_record_snapshot(self.settings),
                      "workflow_mode": "selected_changes", "reconciliation_mode": "preserve"},
            validation={"status": "passed"},
        )
        ArtifactRevision.objects.create(
            publication_batch=self.batch, artifact_type="bin", revision=1,
            file_reference=str(self.candidate_path), content_hash=self.candidate_hash,
            manifest={"items": [item], "introduced_resource_references": [101],
                      "mutation_plan_hash": self.batch.mutation_plan_hash},
            validation={"status": "passed", "target_id": self.target.pk,
                        "source_nmg_hash": self.nmg_hash, "occurrences": [item]},
            scope="change_set", base_controller_bin_hash=self.base_hash,
        )
        from pubtv.operations.publication_review import publication_review
        review = publication_review(self.batch)
        self.assertFalse(review.get("blockers"), review)
        self.batch.approval_2_snapshot = {**publication_snapshot(self.batch), "review_hash": review["review_hash"]}
        self.batch.approval_2_hash = canonical_hash(self.batch.approval_2_snapshot)
        self.batch.approval_2_status = "approved"
        self.batch.approval_2_at = datetime.now(timezone.utc)
        self.batch.save()

    def test_stage_activate_observe_and_rollback(self):
        with self.assertRaises(ValueError):
            stage_publication(self.batch.pk, expected_hash="0" * 64,
                              ftp_factory=FakeFTPAdapter, secret_resolver=lambda _: "example")
        operation = stage_publication(self.batch.pk, expected_hash=self.candidate_hash,
                                      ftp_factory=FakeFTPAdapter, secret_resolver=lambda _: "example")
        self.assertEqual(operation.state, "staged")
        self.assertEqual(FakeFTPConnection.files["/internal/schedule/schedule.bin"], self.base_path.read_bytes())
        with self.assertRaises(ValueError):
            stage_publication(self.batch.pk, expected_hash=self.candidate_hash,
                              ftp_factory=FakeFTPAdapter, secret_resolver=lambda _: "example")
        sockets = []
        def socket_factory(address, timeout):
            sock = FakeSocket(); sockets.append(sock); return sock
        operation = activate_publication(operation.pk, expected_hash=self.candidate_hash,
                                         ftp_factory=FakeFTPAdapter, secret_resolver=lambda _: "example",
                                         socket_factory=socket_factory)
        self.assertEqual(operation.state, "activation_acknowledged")
        self.assertEqual(FakeFTPConnection.files["/internal/schedule/schedule.bin"], self.candidate_path.read_bytes())
        self.assertEqual(sum(line.startswith(b"LOADSCH ") for line in sockets[0].sent), 1)
        self.assertEqual(UploadedOccurrenceCoverage.objects.count(), 0)
        evidence = Path(self.directory.name) / "controller-evidence.txt"
        evidence.write_text(json.dumps({
            "target_id": self.target.pk, "artifact_sha256": self.candidate_hash,
            "schedule_path": "/internal/schedule/schedule.bin", "status": "observed_active",
            "observed_at": "2026-10-02T12:00:00-04:00", "source": "operator_observation",
        }))
        operation = observe_activation(operation.pk, evidence_file=str(evidence))
        self.assertEqual(operation.state, "activation_observed")
        self.assertEqual(UploadedOccurrenceCoverage.objects.count(), 1)
        operation = rollback_publication(operation.pk, expected_rollback_hash=self.base_hash,
                                         ftp_factory=FakeFTPAdapter, secret_resolver=lambda _: "example",
                                         socket_factory=socket_factory)
        self.assertEqual(operation.state, "rolled_back")
        self.assertEqual(FakeFTPConnection.files["/internal/schedule/schedule.bin"], self.base_path.read_bytes())
        self.assertEqual(UploadedOccurrenceCoverage.objects.filter(upload__state="active").count(), 0)
        self.assertEqual(ActivationEvidence.objects.filter(publication_batch=self.batch,
                                                            status="activation_observed").count(), 1)

    def test_new_resources_require_registration_qualification(self):
        ResearchGate.objects.filter(target=self.target, key="resource_registration").delete()
        with self.assertRaisesRegex(ValueError, "resource_registration"):
            stage_publication(self.batch.pk, expected_hash=self.candidate_hash,
                              ftp_factory=FakeFTPAdapter, secret_resolver=lambda _: "example")

    def test_database_rejects_second_active_operation_for_target(self):
        artifact = self.batch.artifact_revisions.get(artifact_type="bin")
        ScheduleDeliveryOperation.objects.create(
            publication_batch=self.batch, target=self.target, artifact=artifact,
            approval_hash=self.batch.approval_2_hash, base_hash=self.base_hash,
            state="staged",
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            ScheduleDeliveryOperation.objects.create(
                publication_batch=self.batch, target=self.target, artifact=artifact,
                approval_hash=self.batch.approval_2_hash, base_hash=self.base_hash,
                state="prepared",
            )

    def test_upload_failure_keeps_live_schedule_and_requires_reconciliation(self):
        with self.assertRaises(OSError):
            stage_publication(self.batch.pk, expected_hash=self.candidate_hash,
                              ftp_factory=FailedUploadAdapter, secret_resolver=lambda _: "example")
        operation = self.batch.delivery_operations.get()
        self.assertEqual(operation.state, "ambiguous")
        self.assertEqual(FakeFTPConnection.files["/internal/schedule/schedule.bin"], self.base_path.read_bytes())
        self.assertNotIn("LOADSCH", json.dumps(operation.result))

    def test_authentication_rejection_after_promotion_is_ambiguous_without_loadsch(self):
        operation = stage_publication(self.batch.pk, expected_hash=self.candidate_hash,
                                      ftp_factory=FakeFTPAdapter, secret_resolver=lambda _: "example")
        sockets = []
        def socket_factory(address, timeout):
            sock = AuthenticationRejectedSocket(); sockets.append(sock); return sock
        with self.assertRaises(Exception):
            activate_publication(operation.pk, expected_hash=self.candidate_hash,
                                 ftp_factory=FakeFTPAdapter, secret_resolver=lambda _: "example",
                                 socket_factory=socket_factory)
        operation.refresh_from_db()
        self.assertEqual(operation.state, "ambiguous")
        self.assertEqual(FakeFTPConnection.files["/internal/schedule/schedule.bin"], self.candidate_path.read_bytes())
        self.assertEqual(sum(line.startswith(b"LOADSCH ") for line in sockets[0].sent), 0)

    def test_observation_rejects_unattributed_text(self):
        operation = stage_publication(self.batch.pk, expected_hash=self.candidate_hash,
                                      ftp_factory=FakeFTPAdapter, secret_resolver=lambda _: "example")
        operation = activate_publication(operation.pk, expected_hash=self.candidate_hash,
                                         ftp_factory=FakeFTPAdapter, secret_resolver=lambda _: "example",
                                         socket_factory=lambda *args, **kwargs: FakeSocket())
        evidence = Path(self.directory.name) / "unattributed.txt"
        evidence.write_text("active")
        with self.assertRaises(ValueError):
            observe_activation(operation.pk, evidence_file=str(evidence))
        self.assertEqual(UploadedOccurrenceCoverage.objects.count(), 0)

    def test_observation_rejects_timestamp_without_offset(self):
        operation = stage_publication(self.batch.pk, expected_hash=self.candidate_hash,
                                      ftp_factory=FakeFTPAdapter, secret_resolver=lambda _: "example")
        operation = activate_publication(operation.pk, expected_hash=self.candidate_hash,
                                         ftp_factory=FakeFTPAdapter, secret_resolver=lambda _: "example",
                                         socket_factory=lambda *args, **kwargs: FakeSocket())
        evidence = Path(self.directory.name) / "naive-observation.json"
        evidence.write_text(json.dumps({
            "target_id": self.target.pk, "artifact_sha256": self.candidate_hash,
            "schedule_path": "/internal/schedule/schedule.bin", "status": "observed_active",
            "observed_at": "2026-10-02T12:00:00", "source": "operator_observation",
        }))
        with self.assertRaisesRegex(ValueError, "UTC offset"):
            observe_activation(operation.pk, evidence_file=str(evidence))
        self.assertEqual(UploadedOccurrenceCoverage.objects.count(), 0)

    def test_lost_loadsch_ack_is_ambiguous_without_retry_or_coverage(self):
        operation = stage_publication(self.batch.pk, expected_hash=self.candidate_hash,
                                      ftp_factory=FakeFTPAdapter, secret_resolver=lambda _: "example")
        sockets = []
        def socket_factory(address, timeout):
            sock = TimeoutAfterLoadSocket(); sockets.append(sock); return sock
        with self.assertRaises(Exception):
            activate_publication(operation.pk, expected_hash=self.candidate_hash,
                                 ftp_factory=FakeFTPAdapter, secret_resolver=lambda _: "example",
                                 socket_factory=socket_factory)
        operation.refresh_from_db()
        self.assertEqual(operation.state, "ambiguous")
        self.assertEqual(sum(line.startswith(b"LOADSCH ") for line in sockets[0].sent), 1)
        self.assertEqual(UploadedOccurrenceCoverage.objects.count(), 0)
        self.assertTrue(operation.remote_backup_path)

    def test_failed_promotion_restores_known_good_without_loadsch(self):
        operation = stage_publication(self.batch.pk, expected_hash=self.candidate_hash,
                                      ftp_factory=FakeFTPAdapter, secret_resolver=lambda _: "example")
        def no_command(*args, **kwargs):
            raise AssertionError("LOADSCH must not be sent")
        with self.assertRaises(Exception):
            activate_publication(operation.pk, expected_hash=self.candidate_hash,
                                 ftp_factory=FailedPromotionAdapter, secret_resolver=lambda _: "example",
                                 socket_factory=no_command)
        operation.refresh_from_db()
        self.assertEqual(operation.state, "failed")
        self.assertEqual(FakeFTPConnection.files["/internal/schedule/schedule.bin"], self.base_path.read_bytes())
        self.assertEqual(UploadedOccurrenceCoverage.objects.count(), 0)

    def test_uncertain_rollback_cannot_repeat_loadsch(self):
        operation = stage_publication(self.batch.pk, expected_hash=self.candidate_hash,
                                      ftp_factory=FakeFTPAdapter, secret_resolver=lambda _: "example")
        operation = activate_publication(operation.pk, expected_hash=self.candidate_hash,
                                         ftp_factory=FakeFTPAdapter, secret_resolver=lambda _: "example",
                                         socket_factory=lambda *args, **kwargs: FakeSocket())
        evidence = Path(self.directory.name) / "rollback-observation.json"
        evidence.write_text(json.dumps({
            "target_id": self.target.pk, "artifact_sha256": self.candidate_hash,
            "schedule_path": "/internal/schedule/schedule.bin", "status": "observed_active",
            "observed_at": "2026-10-02T12:00:00-04:00", "source": "operator_observation",
        }))
        operation = observe_activation(operation.pk, evidence_file=str(evidence))
        sockets = []
        def uncertain_socket(address, timeout):
            sock = TimeoutAfterLoadSocket(); sockets.append(sock); return sock
        with self.assertRaises(Exception):
            rollback_publication(operation.pk, expected_rollback_hash=self.base_hash,
                                 ftp_factory=FakeFTPAdapter, secret_resolver=lambda _: "example",
                                 socket_factory=uncertain_socket)
        operation.refresh_from_db()
        self.assertEqual(operation.state, "rollback_ambiguous")
        self.assertEqual(sum(line.startswith(b"LOADSCH ") for line in sockets[0].sent), 1)
        with self.assertRaisesRegex(ValueError, "no reviewed rollback action"):
            rollback_publication(operation.pk, expected_rollback_hash=self.base_hash,
                                 ftp_factory=FakeFTPAdapter, secret_resolver=lambda _: "example",
                                 socket_factory=uncertain_socket)
        self.assertEqual(len(sockets), 1)

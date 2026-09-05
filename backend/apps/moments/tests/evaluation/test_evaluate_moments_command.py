"""The `evaluate_moments` command, end to end.

The behavioural tests matter, but the ones that matter most are the last three
sections: evaluation must change nothing, contact nothing, and print nothing
about the people whose chat it read. Those are the promises that make it safe to
run against a real collected session.
"""

from __future__ import annotations

import json
from datetime import timedelta
from io import StringIO

import httpx
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.clips.models import Clip
from apps.moments.models import MomentCandidate
from apps.monitoring.models import ChatMessage, StreamSession, StreamSessionStatus
from apps.streamers.models import Platform, Streamer
from apps.twitch.models import TwitchConnection

from .conftest import PLAIN_TEXT, REACTION_TEXT, at, calm_baseline, hype_burst, ordered

pytestmark = pytest.mark.django_db

FAKE_ACCESS_TOKEN = "FAKE-ACCESS-TOKEN-do-not-leak-9z8y7x"
FAKE_REFRESH_TOKEN = "FAKE-REFRESH-TOKEN-do-not-leak-6w5v4u"
FAKE_HASH_SECRET = "FAKE-CHAT-HASH-SECRET-do-not-leak-3t2s1r"

EXPERIMENT = {
    "name": "diversity-led",
    "candidate_threshold": 70,
    "weights": {
        "velocity": 0.30,
        "reaction": 0.15,
        "diversity": 0.35,
        "emote": 0.10,
        "absolute_activity": 0.10,
    },
}


# -- fixtures ------------------------------------------------------------------


@pytest.fixture
def session(db) -> StreamSession:
    streamer = Streamer.objects.create(
        platform=Platform.TWITCH,
        platform_user_id="37402112",
        username="shroud",
        display_name="shroud",
        channel_url="https://www.twitch.tv/shroud",
    )
    return StreamSession.objects.create(
        streamer=streamer,
        platform_stream_id="stream-eval",
        started_at=at(0),
        last_observed_at=at(300),
        status=StreamSessionStatus.ENDED,
    )


@pytest.fixture
def collected_chat(session) -> StreamSession:
    """A calm stretch, one clear reaction, then calm again."""
    samples = ordered(
        calm_baseline(start=0.0, end=120.0),
        hype_burst(start=120.0, end=130.0),
        calm_baseline(start=130.0, end=220.0),
    )
    ChatMessage.objects.bulk_create(
        ChatMessage(
            session=session,
            twitch_event_message_id=f"event-{index}",
            twitch_message_id=f"message-{index}",
            chatter_user_id_hash=sample.chatter_hash,
            text=sample.text,
            timestamp=sample.timestamp,
            emote_count=sample.emote_count,
        )
        for index, sample in enumerate(samples)
    )
    return session


@pytest.fixture
def labels_file(tmp_path, session):
    def write(*, seconds=(122.0,), session_id=None, tolerance=15):
        document = {
            "version": 1,
            "session_id": session.pk if session_id is None else session_id,
            "default_tolerance_seconds": tolerance,
            "moments": [{"timestamp": at(value).isoformat()} for value in seconds],
        }
        path = tmp_path / "labels.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return str(path)

    return write


@pytest.fixture
def config_file(tmp_path):
    def write(document=EXPERIMENT, name="experiment.json"):
        path = tmp_path / name
        path.write_text(json.dumps(document), encoding="utf-8")
        return str(path)

    return write


def run(*args, **options) -> str:
    out = StringIO()
    call_command("evaluate_moments", *args, stdout=out, stderr=out, **options)
    return out.getvalue()


# -- the ordinary run ----------------------------------------------------------


def test_a_baseline_evaluation_reports_its_findings(collected_chat, labels_file):
    output = run(str(collected_chat.pk), "--labels", labels_file())

    assert f"Session {collected_chat.pk}" in output
    assert "Configuration: baseline" in output
    assert "precision" in output
    assert "candidates" in output


def test_the_session_summary_reports_only_aggregates(collected_chat, labels_file):
    output = run(str(collected_chat.pk), "--labels", labels_file())

    assert "messages" in output
    assert "labels            1" in output
    assert "cadence" in output


def test_a_labelled_moment_is_found(collected_chat, labels_file):
    output = run(str(collected_chat.pk), "--labels", labels_file(seconds=(122.0,)))

    assert "true positives                1" in output


def test_a_label_nowhere_near_a_candidate_is_a_miss(collected_chat, labels_file):
    output = run(str(collected_chat.pk), "--labels", labels_file(seconds=(20.0,)))

    assert "false negatives 1" in output


def test_the_cadence_can_be_changed(collected_chat, labels_file):
    fine = run(str(collected_chat.pk), "--labels", labels_file(), "--cadence-seconds", "1")
    coarse = run(str(collected_chat.pk), "--labels", labels_file(), "--cadence-seconds", "10")

    assert "cadence           1s" in fine
    assert "cadence           10s" in coarse


def test_threshold_crossings_and_candidates_are_reported_separately(collected_chat, labels_file):
    """The distinction calibration depends on."""
    output = run(str(collected_chat.pk), "--labels", labels_file())

    assert "threshold crossings" in output
    assert "cooldown suppressed" in output
    assert "candidates" in output


# -- comparison ----------------------------------------------------------------


def test_an_experiment_can_be_compared_with_the_baseline(collected_chat, labels_file, config_file):
    output = run(str(collected_chat.pk), "--labels", labels_file(), "--compare", config_file())

    assert "Configuration: baseline" in output
    assert "Configuration: diversity-led" in output
    assert "Ranked by F1" in output


def test_the_ranking_refuses_to_select_a_winner(collected_chat, labels_file, config_file):
    """Ordering is for reading. Promotion is a reviewed decision."""
    output = run(str(collected_chat.pk), "--labels", labels_file(), "--compare", config_file())

    assert "selects nothing" in output


def test_a_single_configuration_prints_no_ranking(collected_chat, labels_file):
    output = run(str(collected_chat.pk), "--labels", labels_file())

    assert "Ranked by F1" not in output


def test_an_experiment_can_replace_the_baseline_entirely(collected_chat, labels_file, config_file):
    output = run(str(collected_chat.pk), "--labels", labels_file(), "--config-file", config_file())

    assert "Configuration: diversity-led" in output
    assert "Configuration: baseline" not in output


def test_two_configurations_sharing_a_name_are_refused(collected_chat, labels_file, config_file):
    duplicate = config_file({**EXPERIMENT}, name="second.json")

    with pytest.raises(CommandError, match="both named"):
        run(
            str(collected_chat.pk),
            "--labels",
            labels_file(),
            "--config-file",
            config_file(),
            "--compare",
            duplicate,
        )


# -- the JSON report -----------------------------------------------------------


def test_a_report_can_be_written(collected_chat, labels_file, tmp_path):
    destination = tmp_path / "report.json"

    output = run(str(collected_chat.pk), "--labels", labels_file(), "--output", str(destination))

    assert destination.exists()
    assert "Report written to" in output
    document = json.loads(destination.read_text(encoding="utf-8"))
    assert document["session"]["id"] == collected_chat.pk


def test_a_written_report_carries_no_chat_or_identity(collected_chat, labels_file, tmp_path):
    destination = tmp_path / "report.json"

    run(str(collected_chat.pk), "--labels", labels_file(), "--output", str(destination))

    serialized = destination.read_text(encoding="utf-8")
    assert PLAIN_TEXT not in serialized
    assert REACTION_TEXT not in serialized
    assert "viewer-" not in serialized
    assert "hype-" not in serialized


def test_no_report_is_written_unless_asked(collected_chat, labels_file, tmp_path):
    run(str(collected_chat.pk), "--labels", labels_file())

    assert list(tmp_path.glob("report*.json")) == []


# -- refusing bad input --------------------------------------------------------


def test_an_unknown_session_is_refused(db, labels_file):
    with pytest.raises(CommandError, match="No stream session with id"):
        run("999999", "--labels", labels_file(session_id=999999))


def test_a_session_with_no_chat_is_refused(session, labels_file):
    with pytest.raises(CommandError, match="no collected chat"):
        run(str(session.pk), "--labels", labels_file())


def test_labels_for_another_session_are_refused(collected_chat, labels_file):
    with pytest.raises(CommandError, match="labels are for session"):
        run(str(collected_chat.pk), "--labels", labels_file(session_id=collected_chat.pk + 1))


def test_a_missing_label_file_is_refused(collected_chat, tmp_path):
    with pytest.raises(CommandError, match="Could not read"):
        run(str(collected_chat.pk), "--labels", str(tmp_path / "absent.json"))


def test_a_malformed_label_file_is_refused(collected_chat, tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{oops", encoding="utf-8")

    with pytest.raises(CommandError, match="not valid JSON"):
        run(str(collected_chat.pk), "--labels", str(path))


def test_an_invalid_configuration_is_refused(collected_chat, labels_file, config_file):
    broken = config_file({"name": "bad", "candidate_threshold": 500})

    with pytest.raises(CommandError, match="between 0 and 100"):
        run(str(collected_chat.pk), "--labels", labels_file(), "--config-file", broken)


def test_an_unknown_built_in_configuration_is_refused(collected_chat, labels_file):
    with pytest.raises(CommandError, match="Unknown configuration"):
        run(str(collected_chat.pk), "--labels", labels_file(), "--config", "aggressive")


@pytest.mark.parametrize("cadence", ["0", "-5"])
def test_a_non_positive_cadence_is_refused(collected_chat, labels_file, cadence):
    with pytest.raises(CommandError, match="greater than zero"):
        run(str(collected_chat.pk), "--labels", labels_file(), "--cadence-seconds", cadence)


# -- evaluation changes nothing ------------------------------------------------


def snapshot() -> dict:
    """Everything an evaluation could conceivably disturb."""
    return {
        "sessions": list(
            StreamSession.objects.order_by("pk").values_list(
                "pk", "status", "ended_at", "last_observed_at", "updated_at"
            )
        ),
        "chat": list(
            ChatMessage.objects.order_by("pk").values_list(
                "pk", "text", "chatter_user_id_hash", "timestamp", "emote_count"
            )
        ),
        "moments": list(
            MomentCandidate.objects.order_by("pk").values_list("pk", "status", "updated_at")
        ),
        "clips": list(
            Clip.objects.order_by("pk").values_list("pk", "twitch_clip_id", "updated_at")
        ),
        "connections": list(
            TwitchConnection.objects.order_by("pk").values_list(
                "pk", "access_token", "refresh_token", "scopes"
            )
        ),
    }


def test_a_full_evaluation_leaves_the_database_untouched(
    collected_chat, labels_file, config_file, tmp_path
):
    """The promise that makes this safe to run repeatedly against real data."""
    TwitchConnection.objects.create(
        twitch_user_id="123456",
        login="example",
        display_name="Example",
        access_token=FAKE_ACCESS_TOKEN,
        refresh_token=FAKE_REFRESH_TOKEN,
        token_expires_at=at(0) + timedelta(hours=4),
        scopes=["clips:edit", "user:read:chat"],
    )
    before = snapshot()

    run(
        str(collected_chat.pk),
        "--labels",
        labels_file(),
        "--compare",
        config_file(),
        "--output",
        str(tmp_path / "report.json"),
    )

    assert snapshot() == before


def test_evaluation_creates_no_moment_candidate(collected_chat, labels_file):
    """Even though the replay finds one. Replay predicts; it does not persist."""
    run(str(collected_chat.pk), "--labels", labels_file())

    assert MomentCandidate.objects.count() == 0


def test_evaluation_creates_no_clip(collected_chat, labels_file):
    run(str(collected_chat.pk), "--labels", labels_file())

    assert Clip.objects.count() == 0


def test_evaluation_creates_no_session_or_chat(collected_chat, labels_file):
    sessions = StreamSession.objects.count()
    messages = ChatMessage.objects.count()

    run(str(collected_chat.pk), "--labels", labels_file())

    assert StreamSession.objects.count() == sessions
    assert ChatMessage.objects.count() == messages


def test_running_twice_gives_the_same_answer(collected_chat, labels_file):
    """It would not, if the first run had left anything behind."""
    first = run(str(collected_chat.pk), "--labels", labels_file())
    second = run(str(collected_chat.pk), "--labels", labels_file())

    assert first == second


# -- evaluation contacts nothing -----------------------------------------------


def test_evaluation_makes_no_http_request(collected_chat, labels_file, monkeypatch):
    """Not just no Twitch call — no outbound HTTP at all.

    The suite-wide `no_real_twitch_http` guard already makes a real Twitch
    request impossible; this is narrower and stricter, and it is asserted here
    because an evaluation may be run against real collected data.
    """

    def refuse(*args, **kwargs):
        raise AssertionError("Evaluation must never make an HTTP request.")

    monkeypatch.setattr(httpx.Client, "send", refuse)

    run(str(collected_chat.pk), "--labels", labels_file())


def test_evaluation_opens_no_websocket(collected_chat, labels_file, monkeypatch):
    import websockets.sync.client

    def refuse(*args, **kwargs):
        raise AssertionError("Evaluation must never open a WebSocket.")

    monkeypatch.setattr(websockets.sync.client, "connect", refuse)

    run(str(collected_chat.pk), "--labels", labels_file())


def test_evaluation_never_reaches_the_twitch_client(collected_chat, labels_file, monkeypatch):
    from apps.twitch.client import TwitchClient

    def refuse(*args, **kwargs):
        raise AssertionError("Evaluation must never construct a Twitch request.")

    monkeypatch.setattr(TwitchClient, "_send", refuse)

    run(str(collected_chat.pk), "--labels", labels_file())


def test_evaluation_never_requests_a_clip(collected_chat, labels_file, monkeypatch):
    import apps.clips.services as clip_services

    def refuse(*args, **kwargs):
        raise AssertionError("Evaluation must never request a clip.")

    monkeypatch.setattr(clip_services, "request_clip", refuse)

    run(str(collected_chat.pk), "--labels", labels_file())


def test_evaluation_never_persists_through_the_production_service(
    collected_chat, labels_file, monkeypatch
):
    """Replay must not reach the service layer that writes candidates."""
    import apps.moments.services as moment_services

    def refuse(*args, **kwargs):
        raise AssertionError("Evaluation must never call the persisting service.")

    monkeypatch.setattr(moment_services, "evaluate_session", refuse)

    run(str(collected_chat.pk), "--labels", labels_file())


# -- evaluation reveals nothing ------------------------------------------------


def test_the_output_carries_no_chat_text(collected_chat, labels_file):
    output = run(str(collected_chat.pk), "--labels", labels_file())

    assert PLAIN_TEXT not in output
    assert REACTION_TEXT not in output


def test_the_output_carries_no_chatter_identity(collected_chat, labels_file):
    output = run(str(collected_chat.pk), "--labels", labels_file())

    assert "viewer-" not in output
    assert "hype-" not in output
    for message in ChatMessage.objects.all()[:5]:
        assert message.chatter_user_id_hash not in output
        assert message.twitch_message_id not in output
        assert message.twitch_event_message_id not in output


def test_the_output_carries_no_token_or_secret(collected_chat, labels_file, settings):
    # Set explicitly: the test settings leave this empty, and an empty needle
    # would make the assertion vacuously true.
    settings.CHAT_USER_HASH_SECRET = FAKE_HASH_SECRET
    TwitchConnection.objects.create(
        twitch_user_id="123456",
        login="example",
        display_name="Example",
        access_token=FAKE_ACCESS_TOKEN,
        refresh_token=FAKE_REFRESH_TOKEN,
        token_expires_at=at(0) + timedelta(hours=4),
        scopes=["clips:edit", "user:read:chat"],
    )

    output = run(str(collected_chat.pk), "--labels", labels_file())

    assert FAKE_ACCESS_TOKEN not in output
    assert FAKE_REFRESH_TOKEN not in output
    assert settings.TWITCH_CLIENT_SECRET not in output
    assert FAKE_HASH_SECRET not in output

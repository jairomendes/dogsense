from enum import StrEnum


class ActivityLabel(StrEnum):
    SLEEPING = "sleeping"
    RESTING = "resting"
    STANDING = "standing"
    WALKING = "walking"
    RUNNING = "running"
    PLAYING = "playing"
    PACING = "pacing"
    LOOKING_AROUND = "looking_around"
    UNKNOWN = "unknown"


class StateLabel(StrEnum):
    RELAXED = "relaxed"
    ENGAGED = "engaged"
    ALERT = "alert"
    STRESS_SIGNALS = "stress_signals"
    INDETERMINATE = "indeterminate"


class MonitoringStatus(StrEnum):
    STARTING = "starting"
    ANALYZING = "analyzing"
    CAMERA_OFFLINE = "camera_offline"
    STREAM_UNSTABLE = "stream_unstable"
    DOG_NOT_VISIBLE = "dog_not_visible"
    MULTIPLE_DOGS_DETECTED = "multiple_dogs_detected"
    INSUFFICIENT_VISIBILITY = "insufficient_visibility"
    SERVICE_DEGRADED = "service_degraded"


class SignalName(StrEnum):
    LOW_MOTION = "low_motion"
    LOOSE_BODY_POSTURE = "loose_body_posture"
    REPETITIVE_MOVEMENT = "repetitive_movement"
    LOWERED_POSTURE = "lowered_posture"
    HEAD_TOWARD_DOOR = "head_toward_door"
    EARS_BACK = "ears_back"
    TAIL_LOW = "tail_low"
    RAPID_DIRECTION_CHANGES = "rapid_direction_changes"
    PLAY_BOW = "play_bow"
    BODY_STILLNESS = "body_stillness"


class TransitionReason(StrEnum):
    INITIAL = "initial"
    STATE_CHANGED = "state_changed"
    UNCHANGED = "unchanged"
    CANDIDATE_PENDING = "candidate_pending"
    DOG_NOT_VISIBLE = "dog_not_visible"
    MULTIPLE_DOGS_DETECTED = "multiple_dogs_detected"
    INSUFFICIENT_VISIBILITY = "insufficient_visibility"
    SERVICE_DEGRADED = "service_degraded"
    CAMERA_OFFLINE = "camera_offline"
    STREAM_UNSTABLE = "stream_unstable"
    STOPPED = "stopped"

/** Static output matching the versioned JSON schemas. Do not add UI-only values here. */

export const ACTIVITY_LABELS = [
  "sleeping",
  "resting",
  "standing",
  "walking",
  "running",
  "playing",
  "pacing",
  "looking_around",
  "unknown",
] as const;

export const STATE_LABELS = [
  "relaxed",
  "engaged",
  "alert",
  "stress_signals",
  "indeterminate",
] as const;

export const MONITORING_STATUSES = [
  "starting",
  "analyzing",
  "camera_offline",
  "stream_unstable",
  "dog_not_visible",
  "multiple_dogs_detected",
  "insufficient_visibility",
  "service_degraded",
] as const;

export const SIGNAL_NAMES = [
  "low_motion",
  "loose_body_posture",
  "repetitive_movement",
  "lowered_posture",
  "head_toward_door",
  "ears_back",
  "tail_low",
  "rapid_direction_changes",
  "play_bow",
  "body_stillness",
] as const;

export type ActivityLabel = (typeof ACTIVITY_LABELS)[number];
export type StateLabel = (typeof STATE_LABELS)[number];
export type MonitoringStatus = (typeof MONITORING_STATUSES)[number];
export type SignalName = (typeof SIGNAL_NAMES)[number];

export interface Classification<TLabel extends string> {
  label: TLabel;
  confidence: number;
}

export interface ObservedSignal {
  name: SignalName;
  confidence: number;
}

export interface BehaviorAnalysisV1 {
  schema_version: "behavior-analysis-v1";
  dog_visible: boolean;
  dogs_detected: number;
  observation_quality: number;
  body_visibility: number;
  face_visibility: number;
  activity: Classification<ActivityLabel>;
  state: Classification<StateLabel>;
  state_scores: Record<StateLabel, number>;
  signals: ObservedSignal[];
  summary: string;
  limitations: string[];
}

export interface StableState extends Classification<StateLabel> {
  duration_seconds: number;
  started_at: string;
}

export interface ObservationQuality {
  dog_visible: boolean;
  dogs_detected: number;
  observation_quality: number;
  body_visibility: number;
  face_visibility: number;
}

export interface AnalysisMetadata {
  schema_version: "behavior-analysis-v1";
  prompt_version: "behavior-observer-v1";
  model: string;
  latency_ms: number;
}

export type TransitionReason =
  | "initial"
  | "state_changed"
  | "unchanged"
  | "candidate_pending"
  | "dog_not_visible"
  | "multiple_dogs_detected"
  | "insufficient_visibility"
  | "service_degraded"
  | "camera_offline"
  | "stream_unstable"
  | "stopped";

export interface StateTransition {
  changed: boolean;
  previous_state: StateLabel | null;
  reason: TransitionReason;
}

export interface WorkerIngestV1 {
  schema_version: "worker-ingest-v1";
  analysis_id: string;
  session_id: string;
  camera_id: string;
  captured_at: string;
  transition_seq: number;
  monitoring_status: MonitoringStatus;
  activity: Classification<ActivityLabel> | null;
  state: StableState | null;
  signals: ObservedSignal[];
  quality: ObservationQuality | null;
  analysis: AnalysisMetadata | null;
  transition: StateTransition;
}

export function isMonitoringStatus(value: string): value is MonitoringStatus {
  return (MONITORING_STATUSES as readonly string[]).includes(value);
}

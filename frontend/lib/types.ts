// Types mirroring the backend Pydantic schemas (Section 10.3).

export interface User {
  id: string;
  email: string;
  full_name: string | null;
  timezone: string;
  region: string;
  locale: string;
  role: string;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  user: User;
}

export interface Preferences {
  default_language: string;
  default_platform: string;
  voice_notes: string | null;
  banned_words: string[];
  emoji_level: number;
  length_preset: string;
  lead_days: number;
  notify_email: boolean;
  autopilot: boolean;
  learning_paused: boolean;
  variety: number;
  default_tones: string[];
  length_mean: number | null;
  length_std: number | null;
  length_n: number;
}

export interface LearnedPreferences {
  scoring_weights: Record<string, number>;
  learned_length: { mean: number | null; std: number | null; n: number };
  preference_vector_norm: number | null;
  tone_preferences: { tone: string; count: number; share: number }[];
  arm_stats: {
    tone: string; structure: string; alpha: number; beta: number;
    mean: number; n_shown: number; n_selected: number;
  }[];
  n_feedback_events: number;
  learning_paused: boolean;
}

export interface Audience { id: string; name: string; description: string | null }
export interface Topic { id: string; name: string; description: string | null; keywords: string[]; user_id: string | null }
export interface Occasion {
  id: string; name: string; month: number; day: number | null;
  date_rule: string | null; region: string; category: string; description: string | null;
}

export interface Plan {
  id: string; name: string; topic_id: string | null; audience_id: string | null;
  tones: string[]; purpose: string; platform: string; language: string;
  length_preset: string; cta_text: string | null; keywords: string[];
  extra_instructions: string | null; occasion: string | null;
  post_day: number; post_time: string; lead_days: number;
  is_active: boolean; auto_generate: boolean; created_at: string;
  next_generate_at: string | null; last_status: string | null;
}

export interface Cycle {
  id: string; plan_id: string; target_month: string; post_at: string;
  generate_at: string; status: CycleStatus; selected_message_id: string | null;
  overrides: Record<string, unknown>; plan_name: string | null;
  platform: string | null; latest_request_id: string | null;
}

export type CycleStatus =
  | "PLANNED" | "GENERATING" | "AWAITING_REVIEW" | "SCHEDULED"
  | "PUBLISHED" | "SKIPPED" | "GENERATION_FAILED" | "NEEDS_ATTENTION";

export interface Features {
  relevance: number; tone: number; personalization: number;
  novelty: number; length: number; history: number;
}

export interface Candidate {
  id: string;
  rank: number | null;
  text: string;
  style_label: string | null;
  tone: string | null;
  score: number | null;
  p_rank1: number | null;
  confidence: "high" | "medium" | "low";
  word_count: number | null;
  char_count: number | null;
  is_fallback: boolean;
  status: string;
  filter_reason: string | null;
  features: Features;
  nearest_past: { id: string | null; cosine: number | null; band: string; preview: string | null; month: string | null };
  explain: { reasons?: string[]; keyword_coverage?: number; intent_cosine?: number; tone_probability?: number; arm?: string[]; style_label?: string };
  selected: boolean;
  final_text: string | null;
}

export interface Stage {
  id: string;
  status: "QUEUED" | "RUNNING" | "DONE" | "FAILED";
  stage: string | null;
  ui_stage: string | null;
  provider: string | null;
  error: string | null;
  latency_ms: number | null;
  is_fallback: boolean;
  candidates: Candidate[];
  arms: string[][];
  retrieval: { n_history?: number; exemplars?: string[]; recent?: string[] };
  embedding_map: {
    candidates: { rank: number | null; x: number; y: number }[];
    intent: { x: number; y: number } | null;
    history: { x: number; y: number }[];
  };
  warnings: string[];
}

export interface GenerateResponse { request_id: string; status: string }

export interface HistoryItem {
  id: string; text: string; month: string | null; topic: string | null;
  tone: string | null; style_label: string | null; platform: string | null;
  status: string; score: number | null; novelty: number | null;
  created_at: string; similarity: number | null;
}

export interface NotificationItem {
  id: string; type: string; title: string; body: string | null;
  cycle_id: string | null; read_at: string | null; created_at: string;
}

export interface Dashboard {
  next_action: {
    title: string; subtitle: string | null; cta_label: string | null;
    cta_href: string | null; status: string | null;
  } | null;
  this_month: UpcomingCycle | null;
  upcoming: UpcomingCycle[];
  quick_stats: {
    messages_generated: number; selection_rate: number | null;
    selection_rate_ci: number[] | null; avg_novelty: number | null;
    tone_mix: Record<string, number>; n: number;
  };
  recent_activity: { id: string; kind: string; label: string; created_at: string }[];
}

export interface UpcomingCycle {
  id: string; target_month: string; post_at: string; generate_at: string;
  status: CycleStatus; plan_name: string | null;
}

export interface MetricCard { label: string; value: number | null; detail: string | null; n: number | null }

export interface UsageAnalytics {
  cards: MetricCard[];
  weekly: { week: string; generated: number }[];
  regeneration_rate: number | null;
  edit_rate: number | null;
  avg_time_to_approve_hours: number | null;
}

export interface ContentAnalytics {
  tone_distribution: Record<string, number>;
  topic_distribution: Record<string, number>;
  entropy: number | null;
  entropy_n: number;
  avg_novelty: number | null;
  novelty_trend: { month: string; avg_novelty: number; n: number }[];
  repetition_alerts: { a: string; b: string; cosine: number }[];
  length_distribution: { month: string; words: number | null }[];
  month_tone_heatmap: { month: string; tone: string; count: number }[];
}

export interface QualityAnalytics {
  avg_selected_score: number | null;
  score_selection_correlation: number | null;
  top1_agreement: number | null;
  mrr: number | null;
  latency_p50: number | null;
  latency_p95: number | null;
  llm_failure_rate: number | null;
  fallback_rate: number | null;
  tone_selection_test: { chi2?: number; p_value?: number | null; dof?: number; insufficient?: boolean };
  selection_by_tone: {
    tone: string; n: number; selected: number; rate: number; ci: number[];
  }[];
  n: number;
}

export interface MessageDetail {
  message: {
    id: string; text: string; tone: string | null; style_label: string | null;
    score: number | null; status: string; rank: number | null;
    word_count: number | null; nearest_cosine: number | null;
    features: Features; explain: Record<string, unknown>; created_at: string;
  };
  batch: { id: string; text: string; rank: number | null; score: number | null; status: string; style_label: string | null }[];
  inputs: Record<string, unknown>;
  chosen: string | null;
  feedback: { id: string; event_type: string; reward: number; reason: string | null; edit_distance: number | null; created_at: string }[];
}

export const TONES = ["warm", "playful", "professional", "inspirational", "witty", "grateful", "urgent", "formal"] as const;
export const PURPOSES = ["inform", "promote", "celebrate", "thank", "engage", "announce"] as const;
export const PLATFORMS = ["instagram", "x", "linkedin", "facebook", "whatsapp", "email"] as const;
export const LENGTH_PRESETS = ["short", "medium", "long"] as const;
export const REJECT_REASONS = ["too_long", "too_formal", "too_casual", "too_similar", "off_topic", "wrong_facts", "other"] as const;

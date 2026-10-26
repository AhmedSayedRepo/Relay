export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };

export type Page<T = Record<string, Json>> = {
  items: T[];
  total: number;
  limit: number;
  offset: number;
};

export type Transaction = {
  id: string;
  job_id: string;
  object_id: string;
  device_id: string;
  device_name: string;
  receipt_type: string;
  review_status: string;
  processing_status: string;
  processing_failure: string | null;
  amount: string | null;
  currency: string | null;
  transaction_occurred_at: string | null;
  reference_original: string | null;
  sender_name: string | null;
  recipient_name: string | null;
  duplicate_group_id: string | null;
  duplicate_of_candidate_id: string | null;
  duplicate_reason: string | null;
  is_canonical: boolean;
  source_type: string;
  received_at: string;
  created_at: string;
  ocr_mean_confidence: number | null;
  image_url: string;
  [key: string]: Json;
};

export type DashboardData = {
  date_from: string;
  date_to: string;
  final_transaction_count: number;
  reviewed_count: number;
  needs_review_count: number;
  duplicate_group_count: number;
  duplicate_candidate_count: number;
  processing_failure_count: number;
  pending_processing_count: number;
  receipt_image_activity: number;
  sms_activity: number;
  totals_by_currency: { currency: string; amount: string; transaction_count: number }[];
  processing_by_status: { status: string; count: number }[];
  activity_by_source: { source: string; count: number }[];
  transactions_by_device: { device_id: string; device_name: string; count: number }[];
};

export type DuplicateGroup = {
  group_id: string;
  canonical_candidate_id: string;
  reference_original: string | null;
  amount: string | null;
  currency: string | null;
  device_name: string;
  group_size: number;
  created_at: string;
  [key: string]: Json;
};

export type Job = {
  id: string;
  status: string;
  failure_code: string | null;
  attempt_count: number;
  max_attempts: number;
  next_attempt_at: string;
  object_id: string;
};

"use client";

/* /review: resolves the cycle awaiting review to its latest request. */
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { http } from "@/lib/api";
import type { Cycle, Stage } from "@/lib/types";
import { CardSkeleton, EmptyState } from "@/components/ui";

export default function ReviewIndexPage() {
  const router = useRouter();
  const { data, isLoading } = useQuery({
    queryKey: ["cycles", "review"],
    queryFn: () => http.get<Cycle[]>("/cycles?status=AWAITING_REVIEW"),
  });

  // A cycle can outlive the batch it points at (regenerated, deleted, or wiped
  // by a restart). Only follow a pointer the API still serves, otherwise the
  // redirect lands on a review page that 404s.
  const requestIds = useMemo(
    () => (data ?? []).map((c) => c.latest_request_id).filter((id): id is string => !!id),
    [data],
  );
  const [skip, setSkip] = useState(0);
  const candidateId = requestIds[skip] ?? null;

  const { data: batch, isError: batchMissing } = useQuery({
    queryKey: ["request", candidateId],
    queryFn: () => http.get<Stage>(`/generation-requests/${candidateId}`),
    enabled: !!candidateId,
    retry: false, // a missing batch stays missing
  });

  useEffect(() => {
    if (batch) router.replace(`/review/${batch.id}`);
  }, [batch, router]);

  useEffect(() => {
    if (batchMissing) setSkip((n) => n + 1); // stale pointer: try the next cycle
  }, [batchMissing]);

  // Still fetching, or validating/redirecting to the batch that is waiting:
  // keep the skeleton up so the empty state never flashes before the redirect.
  if (isLoading || !data || candidateId) return <CardSkeleton />;

  return (
    <EmptyState
      title="Nothing waiting for review"
      subtitle="When the scheduler prepares your monthly post, the three ranked candidates appear here."
      action={<button className="btn-primary" onClick={() => router.push("/create")}>Create a post now</button>}
    />
  );
}

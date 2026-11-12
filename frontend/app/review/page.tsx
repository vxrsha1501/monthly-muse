"use client";

/* /review: resolves the cycle awaiting review to its latest request. */
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { http } from "@/lib/api";
import type { Cycle } from "@/lib/types";
import { CardSkeleton, EmptyState } from "@/components/ui";

export default function ReviewIndexPage() {
  const router = useRouter();
  const { data, isLoading } = useQuery({
    queryKey: ["cycles", "review"],
    queryFn: () => http.get<Cycle[]>("/cycles?status=AWAITING_REVIEW"),
  });

  const pending = data?.find((c) => c.latest_request_id) ?? null;

  useEffect(() => {
    if (pending?.latest_request_id) router.replace(`/review/${pending.latest_request_id}`);
  }, [pending, router]);

  // Still fetching, or redirecting to the batch that is waiting: keep the
  // skeleton up so the empty state never flashes before the navigation.
  if (isLoading || !data || pending) return <CardSkeleton />;

  return (
    <EmptyState
      title="Nothing waiting for review"
      subtitle="When the scheduler prepares your monthly post, the three ranked candidates appear here."
      action={<button className="btn-primary" onClick={() => router.push("/create")}>Create a post now</button>}
    />
  );
}

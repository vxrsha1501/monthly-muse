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

  useEffect(() => {
    if (!data) return;
    const pending = data.find((c) => c.latest_request_id);
    if (pending) router.replace(`/review/${pending.latest_request_id}`);
  }, [data, router]);

  if (isLoading) return <CardSkeleton />;

  return (
    <EmptyState
      title="Nothing waiting for review"
      subtitle="When the scheduler prepares your monthly post, the three ranked candidates appear here."
      action={<button className="btn-primary" onClick={() => router.push("/create")}>Create a post now</button>}
    />
  );
}

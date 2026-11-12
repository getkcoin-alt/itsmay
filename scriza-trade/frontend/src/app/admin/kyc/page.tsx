"use client";

import { useState } from "react";
import { Button, Card, Empty, Input, Modal, PageHeader, Segmented, Skeleton, StatusBadge, Table, Td, Th } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api, useApi } from "@/lib/api";
import { dateTime } from "@/lib/format";
import type { Kyc } from "@/lib/types";

type Rec = Kyc & { user_id: string; email: string; full_name: string };

export default function AdminKyc() {
  const [status, setStatus] = useState<"PENDING" | "APPROVED" | "REJECTED" | "NOT_SUBMITTED">("PENDING");
  const { data, mutate } = useApi<{ records: Rec[] }>(`/api/admin/kyc?status=${status}`);
  const [review, setReview] = useState<Rec | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const decide = async (decision: "APPROVED" | "REJECTED") => {
    if (!review) return;
    setBusy(true);
    try {
      await api(`/api/admin/kyc/${review.user_id}`, { method: "POST", json: { decision, note: note || null } });
      toast({ kind: "success", title: `KYC ${decision.toLowerCase()}`, body: review.email });
      setReview(null);
      setNote("");
      mutate();
    } catch (e) {
      toast({ kind: "error", title: "Review failed", body: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-5">
      <PageHeader title="KYC review" subtitle="Simulated identity records. No real documents are collected; PAN values are stored masked." />
      <Card>
        <div className="mb-4"><Segmented value={status} onChange={setStatus} options={[{ value: "PENDING", label: "Pending" }, { value: "APPROVED", label: "Approved" }, { value: "REJECTED", label: "Rejected" }, { value: "NOT_SUBMITTED", label: "Not submitted" }]} /></div>
        {!data ? <Skeleton className="h-60" /> : data.records.length === 0 ? <Empty title="No records in this queue" /> : (
          <Table>
            <thead><tr><Th>User</Th><Th>Legal name</Th><Th>PAN (masked)</Th><Th>City</Th><Th>F&O exp.</Th><Th>Submitted</Th><Th>Status</Th><Th align="right" /></tr></thead>
            <tbody>
              {data.records.map((r) => (
                <tr key={r.user_id} className="hover:bg-ink-800/60">
                  <Td><div className="font-medium text-fg">{r.full_name}</div><div className="text-xs text-fg-muted">{r.email}</div></Td>
                  <Td>{r.legal_name ?? "—"}</Td>
                  <Td className="font-mono text-xs">{r.pan_masked ?? "—"}</Td>
                  <Td>{r.city ?? "—"}</Td>
                  <Td>{r.derivatives_experience ?? "—"}</Td>
                  <Td className="text-fg-muted">{dateTime(r.submitted_at)}</Td>
                  <Td><StatusBadge status={r.status} /></Td>
                  <Td align="right">{r.status === "PENDING" && <Button size="sm" variant="primary" onClick={() => setReview(r)}>Review</Button>}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
      <Modal open={!!review} onClose={() => setReview(null)} title="Review simulated KYC" footer={<div className="flex gap-2"><Button variant="danger" className="flex-1" loading={busy} onClick={() => decide("REJECTED")}>Reject</Button><Button variant="buy" className="flex-1" loading={busy} onClick={() => decide("APPROVED")}>Approve</Button></div>}>
        {review && (
          <div className="space-y-3 text-sm">
            <dl className="grid grid-cols-2 gap-3">
              {[["Name", review.legal_name], ["Email", review.email], ["PAN", review.pan_masked], ["City", review.city], ["Occupation", review.occupation], ["Income", review.income_band], ["F&O experience", review.derivatives_experience], ["Submitted", dateTime(review.submitted_at)]].map(([k, v]) => (
                <div key={k as string}><dt className="text-xs text-fg-muted">{k}</dt><dd className="text-fg">{v ?? "—"}</dd></div>
              ))}
            </dl>
            <Input label="Review note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
          </div>
        )}
      </Modal>
    </div>
  );
}

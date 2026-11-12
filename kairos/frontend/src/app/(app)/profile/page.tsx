"use client";

import { AlertTriangle, BadgeCheck, RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";
import { useSession } from "@/components/layout/session";
import { Button, Card, ErrorNote, Input, Modal, PageHeader, Select, Skeleton, StatusBadge } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api, refreshAll } from "@/lib/api";
import { dateOnly, dateTime } from "@/lib/format";

export default function ProfilePage() {
  const { user, refresh } = useSession();
  const toast = useToast();
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [saving, setSaving] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
  const [resetting, setResetting] = useState(false);
  useEffect(() => {
    if (user) {
      setName(user.full_name);
      setPhone(user.phone ?? "");
    }
  }, [user]);

  if (!user) return <Skeleton className="h-96 rounded-2xl" />;

  const save = async () => {
    setSaving(true);
    try {
      await api("/api/profile", { method: "PATCH", json: { full_name: name, phone } });
      toast({ kind: "success", title: "Profile updated" });
      refresh();
    } catch (e) {
      toast({ kind: "error", title: "Update failed", body: (e as Error).message });
    } finally {
      setSaving(false);
    }
  };

  const reset = async () => {
    setResetting(true);
    try {
      await api("/api/profile/reset-demo", { method: "POST" });
      await refreshAll();
      toast({ kind: "success", title: "Demo account reset", body: "Balances, positions and history were restored to the starting state." });
      setResetOpen(false);
    } catch (e) {
      toast({ kind: "error", title: "Reset failed", body: (e as Error).message });
    } finally {
      setResetting(false);
    }
  };

  return (
    <div className="space-y-5">
      <PageHeader title="Profile" subtitle="Personal details, simulated KYC and demo account controls." />
      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Personal details">
          <div className="space-y-4">
            <Input label="Full name" value={name} onChange={(e) => setName(e.target.value)} />
            <Input label="Email" value={user.email} disabled hint="Email cannot be changed in the demo" />
            <Input label="Mobile (optional)" value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+91 98xxx xxxxx" />
            <div className="flex items-center justify-between text-xs text-fg-muted">
              <span>Member since {dateOnly(user.created_at)}</span>
              <span>Last login {dateTime(user.last_login_at)}</span>
            </div>
            <Button variant="primary" loading={saving} onClick={save}>Save changes</Button>
          </div>
        </Card>
        <KycCard onDone={refresh} />
      </div>
      <Card title="Demo account">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <p className="max-w-2xl text-sm text-fg-soft">
            Reset your demo account to its starting state. {user.email === "demo@platform.test" ? "The shared demo account is restored to the curated multi-asset portfolio with ₹10,00,000 initial capital." : "All orders, positions and transactions are cleared and ₹10,00,000 of demo cash is credited."}
          </p>
          <Button variant="danger" onClick={() => setResetOpen(true)}><RotateCcw size={15} /> Reset demo account</Button>
        </div>
      </Card>
      <Modal open={resetOpen} onClose={() => setResetOpen(false)} title="Reset demo account?" footer={<div className="flex gap-2"><Button variant="ghost" className="flex-1" onClick={() => setResetOpen(false)}>Cancel</Button><Button variant="danger" className="flex-1" loading={resetting} onClick={reset}>Reset account</Button></div>}>
        <p className="flex gap-2 text-sm text-fg-soft"><AlertTriangle size={16} className="mt-0.5 shrink-0 text-warn" /> This permanently clears your simulated orders, positions and ledger history and cannot be undone.</p>
      </Modal>
    </div>
  );
}

function KycCard({ onDone }: { onDone: () => void }) {
  const { user } = useSession();
  const toast = useToast();
  const [f, setF] = useState({ legal_name: "", pan: "", city: "", occupation: "", income_band: "₹5L–10L", derivatives_experience: "None", acknowledge_simulated: false });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const kyc = user!.kyc;
  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api("/api/profile/kyc", { method: "POST", json: { ...f, pan: f.pan.toUpperCase() } });
      toast({ kind: "success", title: "Simulated KYC submitted", body: "An administrator will review it in the back office." });
      onDone();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title="KYC verification (simulated)" action={<StatusBadge status={kyc.status} />}>
      {kyc.status === "APPROVED" || kyc.status === "PENDING" ? (
        <div className="space-y-3 text-sm">
          <div className="flex items-center gap-2 text-fg">
            <BadgeCheck size={18} className={kyc.status === "APPROVED" ? "text-up" : "text-warn"} />
            {kyc.status === "APPROVED" ? "Verified for simulated trading" : "Awaiting administrator review"}
          </div>
          <dl className="grid grid-cols-2 gap-3 text-[13px]">
            <div><dt className="text-fg-muted">Legal name</dt><dd className="text-fg">{kyc.legal_name}</dd></div>
            <div><dt className="text-fg-muted">PAN (masked)</dt><dd className="font-mono text-fg">{kyc.pan_masked}</dd></div>
            <div><dt className="text-fg-muted">City</dt><dd className="text-fg">{kyc.city}</dd></div>
            <div><dt className="text-fg-muted">F&O experience</dt><dd className="text-fg">{kyc.derivatives_experience}</dd></div>
            <div><dt className="text-fg-muted">Submitted</dt><dd className="text-fg">{dateTime(kyc.submitted_at)}</dd></div>
            <div><dt className="text-fg-muted">Reviewed</dt><dd className="text-fg">{dateTime(kyc.reviewed_at)}</dd></div>
          </dl>
          {kyc.review_note && <p className="text-xs text-fg-muted">Note: {kyc.review_note}</p>}
        </div>
      ) : (
        <div className="space-y-3">
          <div className="flex gap-2 rounded-lg border border-warn/30 bg-warn-soft p-3 text-[12.5px] text-[#F7D79B]">
            <AlertTriangle size={15} className="mt-0.5 shrink-0" />
            Demonstration only — do not enter real identity data. Use a dummy PAN-format value such as ABCDE1234F. Only a masked form is stored.
          </div>
          {kyc.status === "REJECTED" && <p className="text-sm text-down">Previous submission rejected: {kyc.review_note}</p>}
          <div className="grid gap-3 sm:grid-cols-2">
            <Input label="Legal name" value={f.legal_name} onChange={(e) => setF({ ...f, legal_name: e.target.value })} />
            <Input label="Dummy PAN" value={f.pan} maxLength={10} onChange={(e) => setF({ ...f, pan: e.target.value.toUpperCase() })} placeholder="ABCDE1234F" />
            <Input label="City" value={f.city} onChange={(e) => setF({ ...f, city: e.target.value })} />
            <Input label="Occupation" value={f.occupation} onChange={(e) => setF({ ...f, occupation: e.target.value })} />
            <Select label="Annual income band" value={f.income_band} onChange={(e) => setF({ ...f, income_band: e.target.value })}>
              {["Below ₹5L", "₹5L–10L", "₹10L–25L", "Above ₹25L"].map((x) => <option key={x}>{x}</option>)}
            </Select>
            <Select label="Derivatives experience" value={f.derivatives_experience} onChange={(e) => setF({ ...f, derivatives_experience: e.target.value })}>
              {["None", "Under 1 year", "1–3 years", "3+ years"].map((x) => <option key={x}>{x}</option>)}
            </Select>
          </div>
          <label className="flex items-start gap-2 text-[13px] text-fg-soft">
            <input type="checkbox" className="mt-0.5 accent-[#8C9BFF]" checked={f.acknowledge_simulated} onChange={(e) => setF({ ...f, acknowledge_simulated: e.target.checked })} />
            I confirm this is simulated information and contains no real identity documents.
          </label>
          <ErrorNote error={err} />
          <Button variant="primary" loading={busy} onClick={submit}>Submit simulated KYC</Button>
        </div>
      )}
    </Card>
  );
}

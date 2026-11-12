"use client";

import { useState } from "react";
import { Badge, Button, Card, Input, Modal, PageHeader, Segmented, SimBadge, Skeleton, Table, Td, Th, Toggle } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api, useApi } from "@/lib/api";
import { ASSET_LABEL, pct, price, tone } from "@/lib/format";
import { mkt } from "./mkt";

interface Asset {
  symbol: string; name: string; asset_class: string; exchange: string; currency: string; enabled: boolean; tradable: boolean; has_options: boolean;
  ltp: number; change_pct: number; model_price: number; price_multiplier: number; override_from: string | null; volatility: number; decimals: number;
}

export default function AdminAssets() {
  const [cls, setCls] = useState("all");
  const { data, mutate } = useApi<{ assets: Asset[] }>("/api/admin/assets", { refreshInterval: 10000 });
  const [editing, setEditing] = useState<Asset | null>(null);
  const toast = useToast();
  const rows = (data?.assets ?? []).filter((a) => cls === "all" || a.asset_class === cls);
  const toggle = async (a: Asset, enabled: boolean) => {
    try {
      await mkt(a.symbol, { enabled });
      toast({ kind: "success", title: `${a.symbol} ${enabled ? "enabled" : "disabled"}`, body: enabled ? undefined : "New orders will be rejected and it is hidden from markets." });
      mutate();
    } catch (e) {
      toast({ kind: "error", title: "Update failed", body: (e as Error).message });
    }
  };
  return (
    <div className="space-y-5">
      <PageHeader title="Instruments" subtitle="Enable or disable demo instruments and manage simulated prices." actions={<SimBadge />} />
      <Card>
        <div className="mb-4"><Segmented size="sm" value={cls} onChange={setCls} options={["all", "stock", "crypto", "forex", "index"].map((v) => ({ value: v, label: v === "all" ? "All" : ASSET_LABEL[v] }))} /></div>
        {!data ? <Skeleton className="h-96" /> : (
          <Table>
            <thead><tr><Th>Instrument</Th><Th>Class</Th><Th align="right">LTP (sim.)</Th><Th align="right">Day</Th><Th align="right">Model price</Th><Th>Override</Th><Th align="right">Vol.</Th><Th align="center">Enabled</Th><Th align="right" /></tr></thead>
            <tbody>
              {rows.map((a) => (
                <tr key={a.symbol} className="hover:bg-ink-800/60">
                  <Td><div className="font-semibold text-fg">{a.symbol}</div><div className="max-w-[200px] truncate text-xs text-fg-muted">{a.name}</div></Td>
                  <Td><Badge>{ASSET_LABEL[a.asset_class]}</Badge>{a.has_options && <Badge tone="brand" className="ml-1">F&O</Badge>}</Td>
                  <Td align="right" className="text-fg">{price(a.ltp, a.decimals)}</Td>
                  <Td align="right" className={tone(a.change_pct)}>{pct(a.change_pct)}</Td>
                  <Td align="right" className="text-fg-muted">{price(a.model_price, a.decimals)}</Td>
                  <Td>{a.override_from ? <Badge tone="warn">×{a.price_multiplier.toFixed(4)}</Badge> : <span className="text-xs text-fg-faint">—</span>}</Td>
                  <Td align="right" className="text-fg-muted">{(a.volatility * 100).toFixed(0)}%</Td>
                  <Td align="center"><Toggle checked={a.enabled} onChange={(v) => toggle(a, v)} label={`Enable ${a.symbol}`} /></Td>
                  <Td align="right"><Button size="sm" onClick={() => setEditing(a)}>Price</Button></Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
      <PriceModal asset={editing} onClose={() => setEditing(null)} onDone={mutate} />
    </div>
  );
}

function PriceModal({ asset, onClose, onDone }: { asset: Asset | null; onClose: () => void; onDone: () => void }) {
  const [target, setTarget] = useState("");
  const [vol, setVol] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const run = async (body: Record<string, unknown>, msg: string) => {
    if (!asset) return;
    setBusy(true);
    try {
      await mkt(asset.symbol, body);
      toast({ kind: "success", title: msg, body: "Quotes, charts, valuations and fills now use the updated simulated price." });
      onDone();
      onClose();
    } catch (e) {
      toast({ kind: "error", title: "Update failed", body: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal open={!!asset} onClose={onClose} title={asset ? `Simulated price · ${asset.symbol}` : ""}>
      {asset && (
        <div className="space-y-4 text-sm">
          <p className="text-fg-soft">Current simulated LTP {price(asset.ltp, asset.decimals)}. Setting a target applies a multiplier to the deterministic model from now on (0.2× – 5×). The action is audit-logged.</p>
          <Input label="Target price" type="number" value={target} onChange={(e) => setTarget(e.target.value)} placeholder={String(asset.ltp)} />
          <Button variant="primary" className="w-full" loading={busy} disabled={!Number(target)} onClick={() => run({ target_price: target }, `${asset.symbol} repriced`)}>Apply price override</Button>
          {asset.override_from && <Button variant="secondary" className="w-full" loading={busy} onClick={() => run({ clear_override: true }, "Override cleared")}>Clear override (return to model)</Button>}
          <div className="border-t border-ink-700 pt-4">
            <Input label="Annualised volatility (e.g. 0.25 = 25%)" type="number" step="0.01" value={vol} onChange={(e) => setVol(e.target.value)} placeholder={String(asset.volatility)} />
            <Button className="mt-2 w-full" loading={busy} disabled={!Number(vol)} onClick={() => run({ volatility: vol }, "Volatility updated")}>Update volatility</Button>
          </div>
        </div>
      )}
    </Modal>
  );
}

export type AssetClass = "stock" | "crypto" | "forex" | "index" | "option";

export interface Quote {
  symbol: string;
  name: string;
  asset_class: AssetClass;
  exchange: string;
  currency: string;
  sector?: string | null;
  decimals: number;
  tick_size: number;
  lot_size: number;
  min_qty: number;
  tradable: boolean;
  enabled: boolean;
  has_options: boolean;
  ltp: number;
  bid: number;
  ask: number;
  open: number;
  high: number;
  low: number;
  prev_close: number;
  change: number;
  change_pct: number;
  volume: number;
  week52_high: number;
  week52_low: number;
  timestamp: number;
  restriction?: string;
  about?: Record<string, string | number>;
}

export interface OptionQuote {
  symbol: string;
  underlying: string;
  underlying_name: string;
  expiry: string;
  strike: number;
  kind: "CE" | "PE";
  lot_size: number;
  ltp: number;
  prev_close: number;
  change: number;
  change_pct: number;
  iv: number;
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
  spot: number;
  oi: number;
  oi_change_pct: number;
  volume: number;
  expired: boolean;
}

export interface Chain {
  underlying: string;
  underlying_name: string;
  spot: number;
  atm_strike: number;
  expiry: string;
  expiries: string[];
  lot_size: number;
  strike_step: number;
  pcr: number | null;
  rows: { strike: number; CE: OptionQuote; PE: OptionQuote }[];
}

export interface User {
  id: string;
  email: string;
  full_name: string;
  phone: string | null;
  role: "user" | "admin";
  status: string;
  created_at: string;
  last_login_at: string | null;
  last_seen_at: string | null;
  preferences: Record<string, unknown>;
  kyc: Kyc;
}

export interface Kyc {
  status: "NOT_SUBMITTED" | "PENDING" | "APPROVED" | "REJECTED";
  legal_name?: string | null;
  pan_masked?: string | null;
  city?: string | null;
  occupation?: string | null;
  income_band?: string | null;
  derivatives_experience?: string | null;
  submitted_at?: string | null;
  reviewed_at?: string | null;
  review_note?: string | null;
}

export interface Summary {
  currency: string;
  cash_balance: number;
  available_cash: number;
  margin_blocked: number;
  margin_live: number;
  reserved_for_orders: number;
  holdings_value: number;
  short_option_liability: number;
  invested_value: number;
  total_value: number;
  net_deposits: number;
  overall_pnl: number;
  overall_pnl_pct: number;
  day_pnl: number;
  day_pnl_pct: number;
  realized_pnl: number;
  unrealized_pnl: number;
  fees_paid: number;
  open_positions: number;
  allocation: { asset_class: string; value: number }[];
  margin_utilisation_pct: number;
  risk_level: "normal" | "elevated" | "high" | "critical";
}

export interface PositionRow {
  id: string;
  symbol: string;
  name: string;
  asset_class: AssetClass;
  side: "LONG" | "SHORT" | "FLAT";
  quantity: number;
  avg_price: number;
  ltp: number;
  prev_close: number;
  currency: string;
  decimals: number;
  fx_to_inr: number;
  invested: number;
  market_value: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  day_pnl: number;
  realized_pnl: number;
  margin_blocked: number;
  margin_live: number;
  opened_at: string;
  sector?: string;
  lot_size?: number;
  lots?: number;
  underlying?: string;
  strike?: number;
  kind?: "CE" | "PE";
  expiry?: string;
  spot?: number;
  delta?: number;
  iv?: number;
  days_to_expiry?: number;
}

export interface Order {
  id: string;
  symbol: string;
  name: string;
  asset_class: AssetClass;
  side: "BUY" | "SELL";
  order_type: "MARKET" | "LIMIT" | "EXPIRY";
  product: string;
  quantity: number;
  lots: number | null;
  limit_price: number | null;
  status: "OPEN" | "FILLED" | "CANCELLED" | "REJECTED";
  filled_price: number | null;
  filled_at: string | null;
  fees: number;
  reserved_amount: number;
  realized_pnl: number;
  reject_reason: string | null;
  created_at: string;
  user_email?: string;
}

export interface LedgerEntry {
  id: string;
  type: string;
  amount: number;
  balance_after: number;
  description: string;
  reference: string;
  order_id: string | null;
  symbol: string | null;
  method: string | null;
  created_at: string;
  user_email?: string;
}

export interface Warning {
  level: "info" | "warning" | "danger";
  code: string;
  message: string;
}

export interface Preview {
  symbol: string;
  name: string;
  asset_class: AssetClass;
  product: string;
  side: "BUY" | "SELL";
  order_type: string;
  quantity: number;
  lots: number | null;
  lot_size: number;
  price: number;
  market_price: number;
  marketable: boolean;
  currency: string;
  notional_inr: number;
  fees: number;
  cash_delta: number;
  margin_required: number;
  margin_released: number;
  available_before: number;
  available_after: number;
  position_after: number;
  realized_pnl: number;
  warnings: Warning[];
  can_execute: boolean;
  rejection_reason: string | null;
}

export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

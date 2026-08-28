const envelope = __QUOTE_REQUESTS_JSON__;

if (!process.env.ENVIA_SHIPPING_API_TOKEN) {
  throw new Error("ENVIA_SHIPPING_API_TOKEN no está disponible en el servicio seguro.");
}

const endpoint = "https://api.envia.com/ship/rate/";
const records = [];
for (const request of envelope.requests || []) {
  const response = await fetch(endpoint, {
    method: "POST",
    redirect: "error",
    signal: AbortSignal.timeout(45_000),
    headers: {
      accept: "application/json",
      "content-type": "application/json",
      authorization: `Bearer ${process.env.ENVIA_SHIPPING_API_TOKEN}`,
    },
    body: JSON.stringify(request.payload),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    records.push({ sku: request.sku, status: "BLOCKED", error: `ENVIA_RATE_${response.status}` });
    continue;
  }
  const rates = Array.isArray(payload?.data) ? payload.data : [];
  const valid = rates
    .map((rate) => ({
      carrier: String(rate?.carrierDescription || rate?.carrier || "").trim(),
      service: String(rate?.serviceDescription || rate?.service || "").trim(),
      amount: Number(rate?.totalPrice),
      currency: String(rate?.currency || "COP").trim(),
      delivery_estimate: String(rate?.deliveryEstimate || "").trim(),
    }))
    .filter((rate) => Number.isFinite(rate.amount) && rate.amount > 0)
    .sort((left, right) => left.amount - right.amount);
  if (!valid.length) {
    records.push({ sku: request.sku, status: "MISSING", error: "ENVIA_RATE_WITHOUT_POSITIVE_OPTIONS" });
    continue;
  }
  records.push({
    sku: request.sku,
    variant_id: request.variant_id,
    status: "AVAILABLE",
    option_count: valid.length,
    options: valid,
    destination: request.destination_snapshot,
    package: request.package_snapshot,
    observed_at: new Date().toISOString(),
    basis: "CURRENT_ELIGIBLE_RATE_OPTIONS_REQUIRES_SELECTION_POLICY",
    binding: false,
    guide_created: false,
    externalWrites: 0,
  });
}

process.stdout.write(JSON.stringify({
  source: "Envía Shipping API POST /ship/rate/ non-binding",
  records,
  binding: false,
  guide_created: false,
  externalWrites: 0,
}));

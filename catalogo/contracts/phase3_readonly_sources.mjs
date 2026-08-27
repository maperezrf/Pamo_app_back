import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";

const mode = process.argv[2];
const outputPath = process.argv[3];
if (!mode || !outputPath || !["shopify", "envia", "siigo"].includes(mode)) {
  console.error("Uso: node phase3-readonly-sources.mjs <shopify|envia|siigo> <salida.json>");
  process.exit(2);
}

const text = (value) => String(value ?? "").trim();
const hash = (value) => createHash("sha256").update(text(value)).digest("hex");
const numberOrNull = (value) => {
  if (value === null || value === undefined || text(value) === "") return null;
  const parsed = Number(text(value).replaceAll(",", ""));
  return Number.isFinite(parsed) ? parsed : null;
};
const safeWrite = async (payload) => {
  await writeFile(outputPath, `${JSON.stringify(payload, null, 2)}\n`, { mode: 0o600 });
  console.log(JSON.stringify({ ok: true, mode, output: outputPath, summary: payload.summary, externalWrites: 0 }));
};
const required = (names) => {
  const missing = names.filter((name) => !text(process.env[name]));
  if (missing.length) {
    console.log(JSON.stringify({ ok: false, mode, code: "VARIABLES_UNAVAILABLE", missing, externalWrites: 0 }));
    process.exit(3);
  }
};

async function shopifyToken() {
  required(["SHOPIFY_STORE_DOMAIN", "SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET"]);
  const domain = text(process.env.SHOPIFY_STORE_DOMAIN).replace(/^https?:\/\//i, "").replace(/\/.*$/, "");
  if (!/^[a-z0-9][a-z0-9-]*\.myshopify\.com$/i.test(domain)) throw new Error("SHOPIFY_DOMAIN_INVALID");
  const response = await fetch(`https://${domain}/admin/oauth/access_token`, {
    method: "POST",
    headers: { accept: "application/json", "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ grant_type: "client_credentials", client_id: process.env.SHOPIFY_CLIENT_ID, client_secret: process.env.SHOPIFY_CLIENT_SECRET }),
    redirect: "error",
    signal: AbortSignal.timeout(30_000),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || !payload.access_token) throw new Error(`SHOPIFY_AUTH_REJECTED_${response.status}`);
  return { domain, token: payload.access_token };
}

const SHOPIFY_QUERY = `
query MerciPhase3Catalog($first:Int!,$after:String){
  productVariants(first:$first,after:$after,sortKey:ID){
    nodes{
      id sku barcode title price compareAtPrice inventoryPolicy inventoryQuantity updatedAt
      selectedOptions{name value}
      metafields(first:1){nodes{namespace key type jsonValue updatedAt}}
      inventoryItem{
        id tracked requiresShipping unitCost{amount currencyCode}
        measurement{weight{unit value}}
        inventoryLevels(first:1){nodes{id location{id name} quantities(names:["available","reserved","committed","incoming","on_hand"]){name quantity} updatedAt} pageInfo{hasNextPage endCursor}}
      }
      product{
        id title handle vendor productType status tags descriptionHtml updatedAt
        category{id name fullName}
        featuredMedia{preview{image{url altText}}}
        collections(first:1){nodes{id handle title updatedAt}}
        metafields(first:1){nodes{namespace key type jsonValue updatedAt}}
      }
    }
    pageInfo{hasNextPage endCursor}
  }
}`;

async function readShopify() {
  const { domain, token } = await shopifyToken();
  const apiVersion = text(process.env.SHOPIFY_API_VERSION) || "2026-07";
  const all = [];
  const safeErrors = [];
  let after = null;
  let pages = 0;
  let runPages = 0;
  try {
    const prior = JSON.parse(await readFile(outputPath, "utf8"));
    if (prior?.source?.system === "SHOPIFY" && Array.isArray(prior?.data?.productVariants?.nodes)) {
      all.push(...prior.data.productVariants.nodes);
      after = prior.data.productVariants.pageInfo?.hasNextPage ? prior.data.productVariants.pageInfo.endCursor : null;
      pages = Number(prior.pages || 0);
      if (!after) {
        console.log(JSON.stringify({ ok: true, mode, output: outputPath, summary: prior.summary, resumed: false, externalWrites: 0 }));
        return;
      }
    }
  } catch {
    // No prior sanitized snapshot: start the initial cursor read.
  }
  do {
    let response;
    let payload;
    let throttledRetries = 0;
    do {
      response = await fetch(`https://${domain}/admin/api/${apiVersion}/graphql.json`, {
        method: "POST",
        headers: { accept: "application/json", "content-type": "application/json", "x-shopify-access-token": token },
        body: JSON.stringify({ query: SHOPIFY_QUERY, variables: { first: 5, after } }),
        redirect: "error",
        signal: AbortSignal.timeout(45_000),
      });
      payload = await response.json().catch(() => ({}));
      const throttled = Array.isArray(payload.errors) && payload.errors.some((item) => item?.extensions?.code === "THROTTLED");
      if (!throttled || throttledRetries >= 8) break;
      const status = payload?.extensions?.cost?.throttleStatus ?? {};
      const requested = Number(payload?.extensions?.cost?.requestedQueryCost ?? 100);
      const available = Number(status.currentlyAvailable ?? 0);
      const rate = Math.max(Number(status.restoreRate ?? 50), 1);
      const waitMs = Math.min(Math.max(Math.ceil((requested - available) / rate * 1000) + 500, 2000), 30_000);
      await new Promise((resolve) => setTimeout(resolve, waitMs));
      throttledRetries += 1;
    } while (true);
    if (!response.ok || !payload.data?.productVariants) {
      const signatures = (Array.isArray(payload.errors) ? payload.errors : []).map((item) => ({
        code: text(item?.extensions?.code).replace(/[^A-Z0-9_\-]/gi, "_").slice(0, 50) || "UNKNOWN",
        path: Array.isArray(item?.path) ? item.path.map((value) => text(value).replace(/[^A-Z0-9_\-]/gi, "_").slice(0, 40)).join(".") : null,
      })).slice(0, 8);
      const cost = payload?.extensions?.cost ?? {};
      console.log(JSON.stringify({
        ok: false, mode, code: `SHOPIFY_QUERY_REJECTED_${response.status}`, signatures,
        cost: {
          requested: Number(cost.requestedQueryCost ?? 0), actual: Number(cost.actualQueryCost ?? 0),
          available: Number(cost.throttleStatus?.currentlyAvailable ?? 0),
          maximum: Number(cost.throttleStatus?.maximumAvailable ?? 0),
          restore_rate: Number(cost.throttleStatus?.restoreRate ?? 0),
        },
        externalWrites: 0,
      }));
      process.exit(4);
    }
    if (Array.isArray(payload.errors) && payload.errors.length) safeErrors.push({ httpStatus: response.status, errorCount: payload.errors.length });
    const connection = payload.data.productVariants;
    all.push(...(connection.nodes ?? []));
    pages += 1;
    runPages += 1;
    after = connection.pageInfo?.hasNextPage ? connection.pageInfo.endCursor : null;
  } while (after && runPages < 500);
  const count = (selector) => all.filter(selector).length;
  const inventoryLevels = all.reduce((total, row) => total + (row.inventoryItem?.inventoryLevels?.nodes?.length ?? 0), 0);
  await safeWrite({
    source: { system: "SHOPIFY", contract: "Admin GraphQL read-only", observed_at: new Date().toISOString(), api_version: apiVersion },
    data: { productVariants: { nodes: all, pageInfo: { hasNextPage: Boolean(after), endCursor: after } } },
    pages,
    summary: {
      variants: all.length, with_weight: count((row) => row.inventoryItem?.measurement?.weight?.value != null),
      with_unit_cost: count((row) => row.inventoryItem?.unitCost?.amount != null),
      with_compare_at: count((row) => row.compareAtPrice != null),
      with_collections: count((row) => (row.product?.collections?.nodes?.length ?? 0) > 0),
      with_product_metafields: count((row) => (row.product?.metafields?.nodes?.length ?? 0) > 0),
      with_variant_metafields: count((row) => (row.metafields?.nodes?.length ?? 0) > 0),
      inventory_levels: inventoryLevels, safe_error_groups: safeErrors.length, externalWrites: 0,
    },
    externalWrites: 0,
  });
}

const many = (value) => value == null ? [] : Array.isArray(value) ? value : [value];
const firstValue = (root, keys) => {
  const wanted = new Set(keys.map((key) => key.toLowerCase()));
  const queue = [root];
  while (queue.length) {
    const current = queue.shift();
    if (!current || typeof current !== "object") continue;
    for (const [key, value] of Object.entries(current)) {
      if (wanted.has(key.toLowerCase()) && value != null && typeof value !== "object") return value;
      if (value && typeof value === "object") queue.push(value);
    }
  }
  return null;
};
const sanitizedDestination = (root) => ({
  city: text(firstValue(root, ["city", "municipality", "destination_city"])) || null,
  state: text(firstValue(root, ["state", "department", "region", "destination_state"])) || null,
  country: text(firstValue(root, ["country", "country_code", "destination_country"])) || null,
  postal_code_prefix: text(firstValue(root, ["postal_code", "zip_code", "postcode"])).slice(0, 3) || null,
});

async function readEnvia() {
  required(["ENVIA_SHIPPING_API_TOKEN"]);
  const records = [];
  const packageEvidence = [];
  let pages = 0;
  let remoteTotal = 0;
  for (let page = 1; page <= 40; page += 1) {
    const response = await fetch(`https://queries.envia.com/v4/orders?limit=100&page=${page}`, {
      method: "GET", headers: { accept: "application/json", authorization: `Bearer ${process.env.ENVIA_SHIPPING_API_TOKEN}` },
      redirect: "error", signal: AbortSignal.timeout(45_000),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(`ENVIA_QUERY_REJECTED_${response.status}`);
    const orders = Array.isArray(payload.orders_info) ? payload.orders_info : [];
    remoteTotal = Number(payload.totals ?? payload.total ?? remoteTotal) || remoteTotal;
    for (const order of orders) {
      for (const location of many(order?.shipment_data?.locations)) {
        for (const entry of many(location?.packages)) {
          const shipment = entry?.shipment ?? entry?.shipment_info ?? entry ?? {};
          const quote = entry?.quote ?? shipment?.quote ?? {};
          const tracking = text(shipment.tracking_number ?? shipment.trackingNumber ?? shipment.guide_number);
          const products = many(entry?.products ?? entry?.items ?? entry?.product).map((product) => ({
            sku: text(product?.sku ?? product?.product_sku ?? product?.variant_sku),
            quantity: numberOrNull(product?.quantity ?? product?.qty ?? product?.amount) ?? 1,
            product_weight_kg: numberOrNull(product?.product_weight ?? product?.weight_kg ?? product?.weight),
            product_dimensions_cm: {
              length_cm: numberOrNull(product?.product_dimensions?.length ?? product?.dimensions?.length ?? product?.length),
              width_cm: numberOrNull(product?.product_dimensions?.width ?? product?.dimensions?.width ?? product?.width),
              height_cm: numberOrNull(product?.product_dimensions?.height ?? product?.dimensions?.height ?? product?.height),
            },
          })).filter((product) => product.sku);
          const exactSingleSku = products.length === 1 && products[0].quantity === 1;
          const weightKg = numberOrNull(firstValue(entry, ["weight_kg", "weight", "declared_weight"]));
          const dimensions = {
            length_cm: numberOrNull(firstValue(entry, ["length_cm", "length"])),
            width_cm: numberOrNull(firstValue(entry, ["width_cm", "width"])),
            height_cm: numberOrNull(firstValue(entry, ["height_cm", "height"])),
          };
          if (products.length) packageEvidence.push({
            sku: exactSingleSku ? products[0].sku : "",
            exact_single_sku: exactSingleSku,
            products,
            shipped: Boolean(tracking),
            weight_kg: weightKg,
            dimensions,
            observed_at: text(shipment.updated_at ?? shipment.updatedAt ?? order.updated_at ?? order.updatedAt) || new Date().toISOString(),
            order_reference_hash: hash(order?.order?.identifier ?? order?.identifier ?? order?.id),
            evidence_reference: "Envía Ecommerce Queries API v4/orders exact-package read-only",
          });
          if (!tracking) continue;
          const common = {
            external_reference_hash: hash(tracking), order_reference_hash: hash(order?.order?.identifier ?? order?.identifier ?? order?.id),
            sku: exactSingleSku ? products[0].sku : "",
            products,
            destination: sanitizedDestination({ order, location, entry }),
            carrier: text(shipment.carrier ?? shipment.carrier_name ?? shipment.service_name ?? entry?.carrier_name) || null,
            weight_kg: weightKg,
            dimensions,
            observed_at: text(shipment.updated_at ?? shipment.updatedAt ?? order.updated_at ?? order.updatedAt) || new Date().toISOString(),
            evidence_reference: "Envía Queries API v4 orders read-only",
          };
          const actual = numberOrNull(shipment.total_cost ?? shipment.totalCost ?? entry?.total_cost);
          const quoted = numberOrNull(quote.price ?? quote.total ?? shipment.quote_price ?? entry?.quote_price);
          if (quoted != null) records.push({ ...common, basis: "CHECKOUT_ESTIMATE", amount: quoted, currency: text(quote.currency ?? shipment.currency) || "COP" });
          if (actual != null) records.push({ ...common, basis: "REALIZED_GUIDE", amount: actual, currency: text(shipment.currency ?? entry?.currency) || "COP" });
        }
      }
    }
    pages += 1;
    if (orders.length < 100) break;
  }
  await safeWrite({
    source: { system: "ENVIA", endpoint: "Queries API v4/orders", observed_at: new Date().toISOString() }, records, package_evidence: packageEvidence,
    summary: {
      pages, remote_total: remoteTotal, evidence_rows: records.length,
      package_evidence_rows: packageEvidence.length,
      exact_single_sku_packages: packageEvidence.filter((row) => row.exact_single_sku).length,
      ambiguous_packages: packageEvidence.filter((row) => !row.exact_single_sku).length,
      shipped_packages: packageEvidence.filter((row) => row.shipped).length,
      quoted_costs: records.filter((row) => row.basis === "CHECKOUT_ESTIMATE").length,
      realized_costs: records.filter((row) => row.basis === "REALIZED_GUIDE").length,
      with_weight: records.filter((row) => row.weight_kg != null).length,
      with_dimensions: records.filter((row) => Object.values(row.dimensions).every((value) => value != null)).length,
      externalWrites: 0,
    },
    externalWrites: 0,
  });
}

function costCandidates(root, prefix = "") {
  if (!root || typeof root !== "object") return [];
  const results = [];
  for (const [key, value] of Object.entries(root)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (/cost|purchase|average|last_cost|unit_cost/i.test(key) && numberOrNull(value) != null) results.push({ path, value: numberOrNull(value) });
    else if (value && typeof value === "object") results.push(...costCandidates(value, path));
  }
  return results;
}

async function siigoAuth() {
  required(["SIIGO_USERNAME", "SIIGO_ACCESS_KEY", "SIIGO_PARTNER_ID"]);
  const response = await fetch("https://api.siigo.com/auth", {
    method: "POST", headers: { accept: "application/json", "content-type": "application/json", "Partner-Id": process.env.SIIGO_PARTNER_ID },
    body: JSON.stringify({ username: process.env.SIIGO_USERNAME, access_key: process.env.SIIGO_ACCESS_KEY }),
    redirect: "error", signal: AbortSignal.timeout(30_000),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || !payload.access_token) throw new Error(`SIIGO_AUTH_REJECTED_${response.status}`);
  return payload.access_token;
}

async function readSiigo() {
  const token = await siigoAuth();
  const headers = { accept: "application/json", authorization: `Bearer ${token}`, "Partner-Id": process.env.SIIGO_PARTNER_ID };
  const listResponse = await fetch("https://api.siigo.com/v1/products?page=1&page_size=20", { headers, redirect: "error", signal: AbortSignal.timeout(45_000) });
  const listPayload = await listResponse.json().catch(() => ({}));
  if (!listResponse.ok) throw new Error(`SIIGO_LIST_REJECTED_${listResponse.status}`);
  const products = Array.isArray(listPayload.results) ? listPayload.results : [];
  const probes = [];
  for (const product of products) {
    const id = text(product.id);
    if (!id) continue;
    const response = await fetch(`https://api.siigo.com/v1/products/${encodeURIComponent(id)}`, { headers, redirect: "error", signal: AbortSignal.timeout(30_000) });
    const detail = await response.json().catch(() => ({}));
    probes.push({
      sku: text(detail.code ?? product.code), http_status: response.status,
      keys: response.ok ? Object.keys(detail).sort() : [],
      cost_candidates: response.ok ? costCandidates(detail) : [],
      available_quantity: response.ok ? numberOrNull(detail.available_quantity) : null,
      warehouses: response.ok ? many(detail.warehouses).map((warehouse) => ({ id: text(warehouse.id), name: text(warehouse.name), quantity: numberOrNull(warehouse.quantity) })) : [],
    });
  }
  const warehouseResponse = await fetch("https://api.siigo.com/v1/warehouses", { headers, redirect: "error", signal: AbortSignal.timeout(30_000) });
  const warehouses = await warehouseResponse.json().catch(() => []);
  await safeWrite({
    source: { system: "SIIGO", endpoints: ["GET /v1/products/{id}", "GET /v1/warehouses"], observed_at: new Date().toISOString() },
    probes, warehouse_catalog: Array.isArray(warehouses) ? warehouses.map((row) => ({ id: text(row.id), name: text(row.name), active: row.active, has_movements: row.has_movements })) : [],
    summary: {
      products_probed: probes.length, with_cost_candidate: probes.filter((row) => row.cost_candidates.length).length,
      with_warehouse_inventory: probes.filter((row) => row.warehouses.length).length,
      warehouses_status: warehouseResponse.status, warehouse_count: Array.isArray(warehouses) ? warehouses.length : 0,
      externalWrites: 0,
    },
    externalWrites: 0,
  });
}

try {
  if (mode === "shopify") await readShopify();
  if (mode === "envia") await readEnvia();
  if (mode === "siigo") await readSiigo();
} catch (error) {
  console.log(JSON.stringify({ ok: false, mode, code: text(error?.message).replace(/[^A-Z0-9_\-]/gi, "_").slice(0, 120), externalWrites: 0 }));
  process.exit(4);
}

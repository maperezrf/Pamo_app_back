import { readConfig } from "./src/config.js";
import { createDatabase } from "./src/database.js";
import { createMercadoLibreOAuthService } from "./src/mercadolibre-oauth.js";

const API_URL = "https://api.mercadolibre.com";
const INCLUDE_SHIPPING = String(process.env.MERCADOLIBRE_REFRESH_SHIPPING || "true").toLowerCase() !== "false";
const INCLUDE_SELLING_FEES = String(process.env.MERCADOLIBRE_REFRESH_SELLING_FEES || "true").toLowerCase() !== "false";
const INCLUDE_SHIPPING_HISTORY = String(process.env.MERCADOLIBRE_REFRESH_SHIPPING_HISTORY || "true").toLowerCase() !== "false";
const SHIPPING_HISTORY_DAYS = 90;
const SHIPPING_HISTORY_MINIMUM_SAMPLE = 20;

const requestJson = async (accessToken, path, extraHeaders = {}) => {
  const response = await fetch(`${API_URL}${path}`, {
    redirect: "error",
    signal: AbortSignal.timeout(30_000),
    headers: { accept: "application/json", authorization: `Bearer ${accessToken}`, ...extraHeaders },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = String(payload?.message || payload?.error || `HTTP ${response.status}`).slice(0, 300);
    throw new Error(`Mercado Libre rechazó la lectura: ${message}`);
  }
  return payload;
};

const requestJsonOptional = async (accessToken, path, extraHeaders = {}) => {
  try {
    return { ok: true, payload: await requestJson(accessToken, path, extraHeaders), error: null };
  } catch (error) {
    return { ok: false, payload: null, error: String(error?.message || error).slice(0, 300) };
  }
};

const optionalNumber = (value) => {
  if (value == null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

const concurrentMap = async (values, concurrency, mapper) => {
  const result = new Array(values.length);
  let cursor = 0;
  const workers = Array.from({ length: Math.min(concurrency, values.length) }, async () => {
    while (cursor < values.length) {
      const index = cursor;
      cursor += 1;
      result[index] = await mapper(values[index], index);
    }
  });
  await Promise.all(workers);
  return result;
};

const resolveBuyerReferenceCity = async (accessToken) => {
  const country = await requestJson(accessToken, "/classified_locations/countries/CO");
  const state = (country?.states || []).find((row) => row?.name === "Bogotá D.C.");
  if (!state?.id) throw new Error("Mercado Libre no devolvió el estado Bogotá D.C. para el escenario de referencia.");
  const stateDetail = await requestJson(accessToken, `/classified_locations/states/${encodeURIComponent(state.id)}`);
  const city = (stateDetail?.cities || []).find((row) => row?.name === "Chapinero");
  if (!city?.id) throw new Error("Mercado Libre no devolvió la localidad Chapinero para el escenario de referencia.");
  return { id: city.id, name: city.name, state: stateDetail.name, country: "Colombia" };
};

const percentileNearestRank = (values, percentile) => {
  const ordered = values.filter(Number.isFinite).sort((left, right) => left - right);
  if (!ordered.length) return null;
  return ordered[Math.max(Math.ceil(percentile * ordered.length) - 1, 0)];
};

const historicalShippingBenchmarks = async (accessToken, sellerId) => {
  const observedAt = new Date();
  const from = new Date(observedAt.getTime() - SHIPPING_HISTORY_DAYS * 86_400_000);
  const orders = [];
  for (let offset = 0; offset < 5_000; offset += 50) {
    const query = new URLSearchParams({
      seller: String(sellerId),
      "order.date_created.from": from.toISOString(),
      "order.date_created.to": observedAt.toISOString(),
      sort: "date_desc",
      limit: "50",
      offset: String(offset),
    });
    const payload = await requestJson(accessToken, `/orders/search?${query}`);
    const page = Array.isArray(payload?.results) ? payload.results : [];
    orders.push(...page);
    if (orders.length >= Number(payload?.paging?.total || 0) || page.length < 50) break;
  }
  const shipmentIds = [...new Set(orders
    .filter((order) => order?.status !== "cancelled" && order?.shipping?.id)
    .map((order) => String(order.shipping.id)))];
  const observations = await concurrentMap(shipmentIds, 10, async (shipmentId) => {
    const [shipment, costs] = await Promise.all([
      requestJsonOptional(accessToken, `/shipments/${encodeURIComponent(shipmentId)}`),
      requestJsonOptional(
        accessToken,
        `/shipments/${encodeURIComponent(shipmentId)}/costs`,
        { "x-format-new": "true" },
      ),
    ]);
    if (!shipment.ok || !costs.ok) return null;
    const sender = (Array.isArray(costs.payload?.senders) ? costs.payload.senders : [])
      .find((row) => String(row?.user_id) === String(sellerId));
    const sellerCost = optionalNumber(sender?.cost);
    if (sellerCost == null) return null;
    return {
      logistic_type: String(shipment.payload?.logistic_type || "unknown"),
      seller_cost: sellerCost,
    };
  });
  const byLogisticType = {};
  for (const logisticType of [...new Set(observations.filter(Boolean).map((row) => row.logistic_type))].sort()) {
    const costs = observations
      .filter((row) => row?.logistic_type === logisticType)
      .map((row) => row.seller_cost);
    byLogisticType[logisticType] = {
      samples: costs.length,
      p75_seller_cost: percentileNearestRank(costs, 0.75),
    };
  }
  return {
    status: "AVAILABLE",
    days: SHIPPING_HISTORY_DAYS,
    minimum_sample: SHIPPING_HISTORY_MINIMUM_SAMPLE,
    non_cancelled_orders: orders.filter((order) => order?.status !== "cancelled").length,
    unique_shipments: shipmentIds.length,
    by_logistic_type: byLogisticType,
    basis: "MELI_SHIPMENT_COSTS_SENDER_ACTUAL_P75",
    observed_at: observedAt.toISOString(),
    externalWrites: 0,
  };
};

const shippingCostsForItem = async (accessToken, sellerId, item, buyerReferenceCity, history = null) => {
  if (item?.status !== "active") {
    return { status: "NOT_APPLICABLE", reason: `La publicación está ${item?.status || "sin estado"}.` };
  }
  const sellerQuery = new URLSearchParams({
    item_id: String(item.id),
    verbose: "true",
    free_shipping: item?.shipping?.free_shipping ? "true" : "false",
  });
  const collectaQuery = new URLSearchParams(sellerQuery);
  collectaQuery.set("mode", "me2");
  collectaQuery.set("logistic_type", "cross_docking");
  const flexQuery = new URLSearchParams(sellerQuery);
  flexQuery.set("mode", "me2");
  flexQuery.set("logistic_type", "self_service");
  const buyerQuery = new URLSearchParams({ city_to: buyerReferenceCity.id });
  const logisticType = String(item?.shipping?.logistic_type || "").trim();
  const shippingTags = Array.isArray(item?.shipping?.tags) ? item.shipping.tags.map(String) : [];
  const [seller, buyer, flexStatus, collectaQuote] = await Promise.all([
    requestJsonOptional(accessToken, `/users/${encodeURIComponent(sellerId)}/shipping_options/free?${sellerQuery}`),
    requestJsonOptional(accessToken, `/items/${encodeURIComponent(item.id)}/shipping_options?${buyerQuery}`),
    requestJsonOptional(accessToken, `/flex/sites/${encodeURIComponent(item?.site_id || "MCO")}/items/${encodeURIComponent(item.id)}/v2`),
    logisticType === "cross_docking"
      ? requestJsonOptional(accessToken, `/users/${encodeURIComponent(sellerId)}/shipping_options/free?${collectaQuery}`)
      : Promise.resolve({ ok: false, payload: null, error: null }),
  ]);
  const hasFlex = flexStatus.ok
    ? Boolean(flexStatus.payload?.has_flex)
    : shippingTags.includes("self_service_in");
  const flexQuote = hasFlex
    ? await requestJsonOptional(accessToken, `/users/${encodeURIComponent(sellerId)}/shipping_options/free?${flexQuery}`)
    : { ok: false, payload: null, error: null };
  const sellerCoverage = seller.payload?.coverage?.all_country || {};
  const collectaCoverage = collectaQuote.payload?.coverage?.all_country || {};
  const flexCoverage = flexQuote.payload?.coverage?.all_country || {};
  const buyerOptions = Array.isArray(buyer.payload?.options) ? buyer.payload.options : [];
  const buyerOption = buyerOptions.find((option) => option?.display === "recommended") || buyerOptions[0] || null;
  const sellerAmount = optionalNumber(sellerCoverage.list_cost);
  const collectaAmount = optionalNumber(collectaCoverage.list_cost);
  const flexAmount = optionalNumber(flexCoverage.list_cost);
  const buyerAmount = optionalNumber(buyerOption?.cost);
  const buyerListAmount = optionalNumber(buyerOption?.list_cost);
  const sellerHasNoCoverage =
    logisticType === "not_specified" &&
    sellerAmount === 0 &&
    !buyer.ok;
  const currentEstimate =
    !sellerHasNoCoverage && sellerAmount != null && sellerAmount >= 0
      ? sellerAmount
      : null;
  const collectaEstimate = collectaAmount != null && collectaAmount >= 0 ? collectaAmount : null;
  const flexEstimate = flexAmount != null && flexAmount >= 0 ? flexAmount : null;
  const fullEstimate = logisticType === "fulfillment" ? currentEstimate : null;
  const benchmarkFor = (type) => {
    const benchmark = history?.by_logistic_type?.[type] || null;
    const value = optionalNumber(benchmark?.p75_seller_cost);
    return benchmark && Number(benchmark.samples) >= SHIPPING_HISTORY_MINIMUM_SAMPLE && value != null
      ? value
      : null;
  };
  const collectaP75 = benchmarkFor("cross_docking");
  const flexP75 = benchmarkFor("self_service");
  const fullP75 = benchmarkFor("fulfillment");
  const eligibleEstimates = [
    logisticType === "cross_docking" ? collectaEstimate ?? currentEstimate : null,
    hasFlex ? flexEstimate : null,
    logisticType === "fulfillment" ? fullEstimate : null,
    logisticType === "cross_docking" ? collectaP75 : null,
    hasFlex ? flexP75 : null,
    logisticType === "fulfillment" ? fullP75 : null,
  ].filter((value) => value != null);
  const appliedEstimate = eligibleEstimates.length ? Math.max(...eligibleEstimates) : currentEstimate;
  return {
    status: appliedEstimate != null || buyerAmount != null ? "AVAILABLE" : "UNAVAILABLE",
    seller_estimate: appliedEstimate,
    current_seller_estimate: currentEstimate,
    seller_estimate_strategy: "MAX_ELIGIBLE_QUOTE_OR_HISTORICAL_P75",
    seller_currency: sellerCoverage.currency_id || item?.currency_id || "COP",
    billable_weight_grams: optionalNumber(sellerCoverage.billable_weight),
    buyer_charge: buyerAmount != null && buyerAmount >= 0 ? buyerAmount : null,
    buyer_list_cost: buyerListAmount != null && buyerListAmount > 0 ? buyerListAmount : null,
    buyer_currency: buyerOption?.currency_id || item?.currency_id || "COP",
    buyer_service: buyerOption?.name || null,
    buyer_destination: buyer.payload?.destination || {
      city: { id: buyerReferenceCity.id, name: buyerReferenceCity.name },
      state: { name: buyerReferenceCity.state },
      country: { name: buyerReferenceCity.country },
    },
    free_shipping: Boolean(item?.shipping?.free_shipping),
    current_logistic_type: logisticType || null,
    shipping_tags: shippingTags,
    modalities: {
      collecta: {
        eligible: logisticType === "cross_docking",
        seller_estimate: logisticType === "cross_docking" ? collectaEstimate ?? currentEstimate : null,
        historical_p75: logisticType === "cross_docking" ? collectaP75 : null,
        historical_samples: logisticType === "cross_docking" ? history?.by_logistic_type?.cross_docking?.samples ?? 0 : 0,
        logistic_type: "cross_docking",
        basis: "MELI_SHIPPING_OPTIONS_FREE_CROSS_DOCKING",
      },
      flex: {
        eligible: hasFlex,
        seller_estimate: hasFlex ? flexEstimate : null,
        historical_p75: hasFlex ? flexP75 : null,
        historical_samples: hasFlex ? history?.by_logistic_type?.self_service?.samples ?? 0 : 0,
        logistic_type: "self_service",
        basis: "MELI_FLEX_ITEM_STATUS_AND_SHIPPING_OPTIONS_FREE",
      },
      full: {
        eligible: logisticType === "fulfillment",
        seller_estimate: fullEstimate,
        historical_p75: logisticType === "fulfillment" ? fullP75 : null,
        historical_samples: logisticType === "fulfillment" ? history?.by_logistic_type?.fulfillment?.samples ?? 0 : 0,
        logistic_type: "fulfillment",
        basis: "MELI_SHIPPING_OPTIONS_FREE_CURRENT_LISTING",
      },
    },
    basis: {
      seller: "MELI_SHIPPING_OPTIONS_FREE_CURRENT_LISTING",
      buyer: "MELI_ITEM_SHIPPING_OPTIONS_BOGOTA_REFERENCE",
    },
    observed_at: new Date().toISOString(),
    historical_benchmark: history ? {
      days: history.days,
      minimum_sample: history.minimum_sample,
      basis: history.basis,
      observed_at: history.observed_at,
    } : null,
    errors: [
      seller.ok ? null : seller.error,
      buyer.ok ? null : buyer.error,
      flexStatus.ok ? null : flexStatus.error,
      hasFlex && !flexQuote.ok ? flexQuote.error : null,
      logisticType === "cross_docking" && !collectaQuote.ok ? collectaQuote.error : null,
    ].filter(Boolean),
    externalWrites: 0,
  };
};

const listingPriceRows = (payload) => {
  if (Array.isArray(payload)) return payload.flatMap(listingPriceRows);
  return payload && typeof payload === "object" ? [payload] : [];
};

const sellingFeesForItem = async (accessToken, item) => {
  const price = Number(item?.price);
  const siteId = String(item?.site_id || "MCO").trim();
  const categoryId = String(item?.category_id || "").trim();
  const listingTypeId = String(item?.listing_type_id || "").trim();
  if (!Number.isFinite(price) || price <= 0 || !siteId || !categoryId || !listingTypeId) {
    return {
      status: "UNAVAILABLE",
      reason: "Faltan precio, site, categoría o tipo de publicación para calcular el costo de venta.",
    };
  }
  const query = new URLSearchParams({
    price: String(price),
    category_id: categoryId,
    currency_id: String(item?.currency_id || "COP"),
    listing_type_id: listingTypeId,
  });
  const logisticType = String(item?.shipping?.logistic_type || "").trim();
  const shippingMode = String(item?.shipping?.mode || "").trim();
  if (logisticType) query.set("logistic_type", logisticType);
  if (shippingMode) query.set("shipping_mode", shippingMode);
  const response = await requestJsonOptional(
    accessToken,
    `/sites/${encodeURIComponent(siteId)}/listing_prices?${query}`,
  );
  const candidates = listingPriceRows(response.payload);
  const selected = candidates.find((row) => String(row?.listing_type_id || "") === listingTypeId) || candidates[0] || null;
  const details = selected?.sale_fee_details || {};
  const amount = Number(selected?.sale_fee_amount ?? details?.gross_amount);
  const percent = Number(details?.percentage_fee ?? details?.meli_percentage_fee);
  const fixedFee = Number(details?.fixed_fee);
  const financingFee = Number(details?.financing_add_on_fee);
  return {
    status: response.ok && selected ? "AVAILABLE" : "UNAVAILABLE",
    sale_fee_amount: Number.isFinite(amount) ? amount : null,
    percentage_fee: Number.isFinite(percent) ? percent : null,
    fixed_fee: Number.isFinite(fixedFee) ? fixedFee : null,
    financing_add_on_fee: Number.isFinite(financingFee) ? financingFee : null,
    gross_amount: Number.isFinite(Number(details?.gross_amount)) ? Number(details.gross_amount) : null,
    listing_fee_amount: Number.isFinite(Number(selected?.listing_fee_amount)) ? Number(selected.listing_fee_amount) : null,
    currency: String(selected?.currency_id || item?.currency_id || "COP"),
    listing_type_id: listingTypeId,
    category_id: categoryId,
    logistic_type: logisticType || null,
    shipping_mode: shippingMode || null,
    basis: "MELI_LISTING_PRICES_CURRENT_ITEM",
    observed_at: new Date().toISOString(),
    error: response.ok ? null : response.error,
    externalWrites: 0,
  };
};

const chunks = (values, size) => {
  const result = [];
  for (let index = 0; index < values.length; index += size) result.push(values.slice(index, index + size));
  return result;
};

const attributes = (item, variation = null) => [
  ...(Array.isArray(variation?.attributes) ? variation.attributes : []),
  ...(Array.isArray(item?.attributes) ? item.attributes : []),
];

const attributeValue = (rows, ids) => {
  const accepted = new Set(ids);
  const found = rows.find((row) => accepted.has(String(row?.id || "").toUpperCase()));
  return String(found?.value_name || found?.value_id || "").trim();
};

const imageFor = (item, variation = null) => {
  const pictures = Array.isArray(item?.pictures) ? item.pictures : [];
  const variationPicture = Array.isArray(variation?.picture_ids) && variation.picture_ids.length
    ? pictures.find((picture) => String(picture?.id) === String(variation.picture_ids[0]))
    : null;
  return String(
    variationPicture?.secure_url || variationPicture?.url || pictures[0]?.secure_url || pictures[0]?.url || item?.secure_thumbnail || item?.thumbnail || "",
  ).trim();
};

const normalizeItem = (item, shippingCosts = null, sellingFees = null) => {
  const variations = Array.isArray(item?.variations) && item.variations.length ? item.variations : [null];
  return variations.map((variation) => {
    const rows = attributes(item, variation);
    const sku = String(
      variation?.seller_custom_field || attributeValue(rows, ["SELLER_SKU", "SKU"]) || item?.seller_custom_field || "",
    ).trim();
    return {
      external_product_id: String(item?.id || ""),
      external_variant_id: variation?.id == null ? "" : String(variation.id),
      sku,
      barcode: attributeValue(rows, ["GTIN", "EAN", "UPC"]),
      title: String(item?.title || "").trim(),
      brand: attributeValue(rows, ["BRAND"]),
      category: String(item?.category_id || "").trim(),
      state: [item?.status, ...(Array.isArray(item?.sub_status) ? item.sub_status : [])].filter(Boolean).join(" / "),
      price: variation?.price ?? item?.price ?? null,
      inventory_available: variation?.available_quantity ?? item?.available_quantity ?? null,
      currency: String(item?.currency_id || "COP").trim(),
      url: String(item?.permalink || "").trim(),
      image_url: imageFor(item, variation),
      source_updated_at: item?.last_updated || null,
      payload: {
        user_product_id: item?.user_product_id || null,
        catalog_product_id: item?.catalog_product_id || null,
        official_store_id: item?.official_store_id || null,
        listing_type_id: item?.listing_type_id || null,
        condition: item?.condition || null,
        shipping_mode: item?.shipping?.mode || null,
        logistic_type: item?.shipping?.logistic_type || null,
        shipping_tags: Array.isArray(item?.shipping?.tags) ? item.shipping.tags.map(String) : [],
        shipping_dimensions: item?.shipping?.dimensions || null,
        free_shipping: Boolean(item?.shipping?.free_shipping),
        ...(shippingCosts ? { shipping_costs: shippingCosts } : {}),
        ...(sellingFees ? {
          selling_fees: sellingFees,
          profitability: {
            verified: false,
            commission_amount: sellingFees.sale_fee_amount,
            commission_percent: sellingFees.percentage_fee,
            other_cost_amount: null,
            other_cost_labels: [],
            source: "Mercado Libre listing_prices",
            basis: sellingFees.basis,
            observed_at: sellingFees.observed_at,
          },
        } : {}),
      },
    };
  });
};

const config = readConfig();
const pool = createDatabase(config);

try {
  if (!pool) throw new Error("La base OAuth canónica no está disponible.");
  const oauth = createMercadoLibreOAuthService(pool, config);
  const accessToken = await oauth.getAccessToken();
  const profile = await requestJson(accessToken, "/users/me");
  const sellerId = String(profile?.id || "").trim();
  if (!sellerId) throw new Error("Mercado Libre no devolvió seller_id.");

  const ids = [];
  let scrollId = "";
  for (let page = 1; page <= 200; page += 1) {
    const query = new URLSearchParams({ search_type: "scan", limit: "100" });
    if (scrollId) query.set("scroll_id", scrollId);
    const result = await requestJson(accessToken, `/users/${encodeURIComponent(sellerId)}/items/search?${query}`);
    const pageIds = Array.isArray(result?.results) ? result.results.map(String) : [];
    if (!pageIds.length) break;
    ids.push(...pageIds);
    scrollId = String(result?.scroll_id || scrollId || "");
    process.stderr.write(`Mercado Libre read-only: ${ids.length} publicaciones identificadas. externalWrites=0.\n`);
    if (!scrollId || pageIds.length < 100) break;
  }

  const uniqueIds = [...new Set(ids)];
  if (!uniqueIds.length) throw new Error("La lectura completa devolvió cero publicaciones; se preservará el snapshot local anterior.");

  const itemGroups = await concurrentMap(chunks(uniqueIds, 20), 6, async (group) => {
    const result = await requestJson(accessToken, `/items?ids=${encodeURIComponent(group.join(","))}`);
    return Array.isArray(result) ? result.filter((entry) => Number(entry?.code) === 200 && entry?.body).map((entry) => entry.body) : [];
  });
  const items = itemGroups.flat();
  process.stderr.write(`Mercado Libre multiget: ${items.length}/${uniqueIds.length} publicaciones leídas. externalWrites=0.\n`);
  if (items.length !== uniqueIds.length) {
    throw new Error(`El multiget quedó incompleto (${items.length}/${uniqueIds.length}); no se reemplazará el snapshot local.`);
  }

  let shippingRows = [];
  let shippingByItem = new Map();
  if (INCLUDE_SHIPPING) {
    const buyerReferenceCity = await resolveBuyerReferenceCity(accessToken);
    let shippingHistory = null;
    if (INCLUDE_SHIPPING_HISTORY) {
      try {
        shippingHistory = await historicalShippingBenchmarks(accessToken, sellerId);
        process.stderr.write(`Mercado Libre historial de envío: ${shippingHistory.unique_shipments} envíos no cancelados analizados en ${shippingHistory.days} días; P75 por modalidad disponible. externalWrites=0.\n`);
      } catch (error) {
        process.stderr.write(`Mercado Libre historial de envío: no disponible (${String(error?.message || error).slice(0, 200)}); se conservarán cotizaciones por publicación. externalWrites=0.\n`);
      }
    }
    process.stderr.write(`Mercado Libre costos de envío: consultando ${items.length} publicaciones; escenario ${buyerReferenceCity.state} / ${buyerReferenceCity.name}. externalWrites=0.\n`);
    shippingRows = await concurrentMap(items, 6, (item) => shippingCostsForItem(accessToken, sellerId, item, buyerReferenceCity, shippingHistory));
    shippingByItem = new Map(items.map((item, index) => [String(item.id), shippingRows[index]]));
  } else {
    process.stderr.write("Mercado Libre envío: se conserva el caché anterior; esta actualización prioriza catálogo, SKU, precio e inventario. externalWrites=0.\n");
  }

  let sellingFeeRows = [];
  let sellingFeesByItem = new Map();
  if (INCLUDE_SELLING_FEES) {
    process.stderr.write(`Mercado Libre costos de venta: consultando ${items.length} publicaciones con listing_prices. externalWrites=0.\n`);
    sellingFeeRows = await concurrentMap(items, 6, (item) => sellingFeesForItem(accessToken, item));
    sellingFeesByItem = new Map(items.map((item, index) => [String(item.id), sellingFeeRows[index]]));
  }

  const records = items.flatMap((item) => normalizeItem(
    item,
    shippingByItem.get(String(item.id)),
    sellingFeesByItem.get(String(item.id)),
  ));
  const statuses = Object.fromEntries([...new Set(items.map((item) => item?.status || "unknown"))].sort().map((status) => [
    status,
    items.filter((item) => (item?.status || "unknown") === status).length,
  ]));
  process.stderr.write(`Mercado Libre completo: ${items.length} publicaciones, ${records.length} filas normalizadas; estados ${JSON.stringify(statuses)}. externalWrites=0.\n`);
  if (INCLUDE_SHIPPING) process.stderr.write(`Mercado Libre envío: ${shippingRows.filter((row) => row?.seller_estimate != null).length} costos vendedor y ${shippingRows.filter((row) => row?.buyer_charge != null).length} cobros comprador de referencia. externalWrites=0.\n`);
  if (INCLUDE_SELLING_FEES) process.stderr.write(`Mercado Libre costos de venta: ${sellingFeeRows.filter((row) => row?.sale_fee_amount != null).length}/${sellingFeeRows.length} tarifas disponibles. externalWrites=0.\n`);
  process.stdout.write(JSON.stringify({
    channel: "MERCADO_LIBRE",
    complete: true,
    observed_at: new Date().toISOString(),
    source: "Mercado Libre seller items search_type=scan + multiget via OAuth cifrado de Pamo Maestro",
    records,
  }));
} finally {
  await pool.end();
}

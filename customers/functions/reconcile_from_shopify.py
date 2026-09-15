from integrations.shopify.functions.list_customers_page import list_customers_page

from .upsert_from_shopify_customer import upsert_from_shopify_customer


def reconcile_from_shopify(params=None, progress_callback=None, cancellation_token=None):
    """Pagina TODOS los clientes de Shopify y hace upsert de cada uno en el
    directorio local. Red de seguridad para webhooks perdidos o fallidos, y
    backfill inicial la primera vez que corre -- no hace falta un script
    aparte para los clientes que ya tenían la cédula en `company` antes de
    este proyecto.

    Registrado en `orchestrator` como proceso `customers.reconcile_shopify`
    (ver docs/apps/orchestrator.md), programado a las 2am. Contrato de
    proceso: `(params, progress_callback=None, cancellation_token=None)`.
    """
    progress_callback = progress_callback or (lambda percent, step=None: None)

    cursor = None
    page_number = 0
    total_upserted = 0
    while True:
        page = list_customers_page(cursor=cursor)
        for customer_data in page["customers"]:
            upsert_from_shopify_customer(customer_data)
            total_upserted += 1

        page_number += 1
        # No se conoce el total de páginas de antemano -- se limita a 99
        # para no reportar "100%" antes de terminar de verdad.
        progress_callback(min(99, page_number), f"Página {page_number} -- {total_upserted} clientes sincronizados")

        if cancellation_token:
            cancellation_token.raise_if_cancelled()

        if not page["has_next_page"]:
            break
        cursor = page["end_cursor"]

    progress_callback(100, f"Completado -- {total_upserted} clientes sincronizados")

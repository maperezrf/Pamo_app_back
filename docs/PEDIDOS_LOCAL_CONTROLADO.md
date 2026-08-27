# Pedidos local controlado

## Alcance de esta rama

- Rama: `feature/pedidos-local-controlado-20260827`.
- Base: `main` en `0dbb1b73981aac93ab5faa775180c1ab9558a5f1`.
- Punto previo recuperable: `checkpoint/pre-pedidos-local-20260827-back`.
- Persistencia: SQLite local ignorado por Git y documentos privados bajo
  `private_uploads/`.
- Datos iniciales: casos ficticios/sanitizados; no es una copia de Producción.
- Escrituras externas: deshabilitadas (`ORDERS_EXTERNAL_WRITES_ENABLED=False`).
- Lecturas externas: deshabilitadas (`ORDERS_EXTERNAL_READS_ENABLED=False`).

Esta rama no comparte código ni base de datos con las ramas locales de
Remisiones, Facturación o Multicanal. No se ha hecho push, merge ni despliegue.

## Contrato funcional disponible

- Una fila por pedido, incluso cuando existen varias bodegas o despachos.
- Bodega por SKU y despacho, con bloqueo y auditoría de cambios manuales.
- Filtros separados y combinados para pedidos sin guía y con guía sin
  trazabilidad.
- Carga manual privada de PDF/JPG/PNG, con tamaño máximo de 10 MB y hash SHA-256.
- Preparación manual de WhatsApp por cada contacto activo; la guía no es
  obligatoria y no existe envío automático.
- Filtros guardados, estados de integración y bitácora logística.
- Control de concurrencia por versión para evitar sobrescrituras silenciosas.

## Puertas de seguridad

1. El endpoint de sincronización externa responde con bloqueo en modo local.
2. Los adaptadores de Shopify, Envía, Mercado Libre, Falabella y Sodimac son
   contratos inactivos; no contienen credenciales.
3. Sodimac no inicia un consumidor paralelo: una futura integración debe leer
   el modelo canónico existente para evitar duplicados con el proceso de Miguel
   Ángel.
4. La sesión demo sólo funciona cuando `DEBUG=True` y
   `LOCAL_DEMO_AUTH_ENABLED=True`.
5. Las guías se sirven por una ruta autenticada; no se publica `MEDIA_URL`.
6. No habilitar proveedores reales ni copiar secretos a este documento.

## Inicio local

Usar un archivo `.env` local con valores de desarrollo y mantener:

```text
DATABASE_URL=
ORDERS_LOCAL_MODE=True
ORDERS_EXTERNAL_READS_ENABLED=False
ORDERS_EXTERNAL_WRITES_ENABLED=False
LOCAL_DEMO_AUTH_ENABLED=True
```

Después:

```text
python manage.py migrate
python manage.py seed_orders_local
python manage.py runserver 127.0.0.1:8012
```

El puerto 8012 se usa para no interferir con los entornos locales que ya ocupan
8010 y 8011. Para operación normal, el modo demo debe volver a quedar apagado.

## Siguiente fase permitida

Conectar primero lecturas reales en un entorno separado, una fuente por vez,
con prueba de identidad, idempotencia y reconciliación. Shopify debe ser la
fuente de pedido; Envía/Fulfillment complementa guía y costo; Sodimac debe
consumirse desde el modelo canónico. Las escrituras, Beta y Producción requieren
autorizaciones nuevas y explícitas.


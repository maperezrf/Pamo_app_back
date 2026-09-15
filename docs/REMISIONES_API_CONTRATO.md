# Contrato API — Remisiones y facturación

Base: `/api/facturacion/`. Autenticación por cookie de sesión Django y CSRF.

## Roles

- Operación: `Admin`, `Operaciones`, `Logistica`.
- Contabilidad: `Admin`, `Facturacion`.
- El backend aplica el control; ocultar botones en React no sustituye los permisos.

## Endpoints implementados en fase 1

| Método | Ruta | Permiso | Resultado |
|---|---|---|---|
| GET | `remisiones/referencias/` | Operación | Bodegas y favoritos locales. |
| GET | `remisiones/` | Operación | Hasta 200 remisiones; admite `invoice_status`. Oculta costo/SKU privado. |
| POST | `remisiones/` | Operación | Crea borrador con líneas y entrega. |
| GET | `remisiones/<uuid>/` | Operación o contabilidad | Detalle; campos privados solo para contabilidad/admin. |
| POST | `remisiones/<uuid>/confirmar/` | Operación | Confirma con `{ "expected_version": n }`; asigna consecutivo transaccional. |
| GET | `remisiones/contabilidad/` | Contabilidad | Cola con datos privados de preparación. |
| GET | `remisiones/<uuid>/factura/vista-previa/` | Contabilidad | Valida SKU/precio y devuelve vista previa sin escribir en Siigo. |
| POST | `remisiones/<uuid>/factura/confirmar/` | Contabilidad | Bloqueado salvo doble bandera; en fase 1 no emite. |

## Forma mínima de creación

```json
{
  "warehouse": 1,
  "supplier": 2,
  "customer": 1,
  "requester_name": "ARLEY",
  "requester_document": "",
  "lines": [
    {
      "quantity": "2.000",
      "original_description": "GRIFERÍA",
      "usage_destination": "CALLE 80"
    }
  ],
  "delivery": {
    "method": "PERSONAL_PICKUP",
    "notes": "Firma posterior por enlace seguro"
  }
}
```

## Concurrencia y errores

- Mutaciones de confirmación exigen `expected_version`.
- Validación: `400` con errores por campo.
- Sin sesión/rol: `403`.
- Recurso inexistente: `404`.
- Escrituras externas desactivadas: `503`, código `EXTERNAL_WRITES_DISABLED`.
- Adaptador financiero aún no migrado: `501`, código `SIIGO_ADAPTER_NOT_MIGRATED`.

## Seguridad financiera

`EXTERNAL_WRITES_ENABLED=False` y `SIIGO_INVOICE_WRITES_ENABLED=False` son los valores por defecto. Esta fase no incluye credenciales Siigo ni autoriza facturas reales.

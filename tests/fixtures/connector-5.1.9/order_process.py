import datetime
import configparser
from datetime import timedelta
import json
import os
import random
import re
import sys
from pathlib import Path
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from connector_logging import get_logger


LOGGER = get_logger("order_process")


def application_dir():
    """Carpeta externa del conector, tanto en fuente como compilado onefile."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BASE_DIR = application_dir()
AGENT_DIR = BASE_DIR / "Agente_FF"

config = configparser.ConfigParser()
try:
    config.read(BASE_DIR / "config.ini", encoding="utf-8-sig")
except UnicodeDecodeError:
    config.read(BASE_DIR / "config.ini", encoding="cp1252")
    LOGGER.warning("config.ini fiscal antiguo leído con codificación CP1252")
except Exception:
    LOGGER.exception("No fue posible cargar la configuración fiscal")
    raise
n_sucursal = config.get("Target", "sucursal", fallback="")
punto_facturacion = config.get("Target", "punto_de_facturacion", fallback="")

MONEY_QUANT = Decimal("0.01")
FEL_PAYMENT_CODES = {f"{code:02d}" for code in range(1, 11)} | {"99"}
INTEGRATION_PAYMENT_CODE_MAP = {
    "11": "08",  # Yappy: transferencia/depósito a cuenta bancaria.
    "12": "99",  # CoalaPay: no está listado; se declara como otro.
}


def to_decimal(value, field_name="importe"):
    """Convierte un valor numérico sin redondearlo."""
    if value is None or value is False or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{field_name} inválido: {value!r}") from exc


def to_money(value, field_name="importe"):
    """Convierte un valor a Decimal de dos posiciones sin errores binarios de float."""
    return to_decimal(value, field_name).quantize(
        MONEY_QUANT,
        rounding=ROUND_HALF_UP,
    )


def money_text(value):
    """Devuelve un importe FEL con exactamente dos decimales."""
    return format(to_money(value), ".2f")


def clean_text_field(value):
    """Evita que el separador | o saltos de línea rompan la estructura del TXT."""
    if value is None or value is False:
        return ""
    return str(value).replace("|", "/").replace("\r", " ").replace("\n", " ").strip()


def normalize_payment_code(value):
    """Normaliza el código de integración o devuelve None cuando no fue enviado."""
    if value is None or value is False or value == "":
        return None

    code = clean_text_field(value)
    if not re.fullmatch(r"\d{1,2}", code):
        raise ValueError(
            f"PAYMENT_CODE inválido: {value!r}. Debe contener uno o dos dígitos."
        )
    return code.zfill(2)


def fel_payment_description(value):
    """Genera la descripción obligatoria de FEL 99 con longitud de 10 a 100."""
    description = clean_text_field(value) or "Otro medio"
    if len(description) < 10:
        description = f"Pago por {description}"
    return description[:100]


def resolve_explicit_payment_code(value, pay_name):
    """Traduce el código de Odoo a uno admitido por el catálogo FEL/Agente."""
    received_code = normalize_payment_code(value)
    if received_code is None:
        return None

    if received_code in FEL_PAYMENT_CODES:
        description = fel_payment_description(pay_name) if received_code == "99" else ""
        return received_code, description

    mapped_code = INTEGRATION_PAYMENT_CODE_MAP.get(received_code, "99")
    description = fel_payment_description(pay_name) if mapped_code == "99" else ""
    LOGGER.warning(
        "PAYMENT_CODE de integración traducido al catálogo FEL | "
        "recibido=%s | metodo=%s | enviado=%s",
        received_code,
        pay_name,
        mapped_code,
    )
    return mapped_code, description


metodo_pago = [
        ('Efectivo','02'),
        ('Cash','02'),
        ('Bank','08'),
        ('TarjetaCredito','03'),
        ('TarjetaDebito','04'),
        ('Yappy','08'),
        ('Transferencia','08'),
        ('Banco','08'),
        ('Fidelidad','05'),
]

########
### Linea 01 Datos del documento electronico
########
tipo_emision = "03"  # Autorización de Uso Posterior, operación normal

tipoDoc = ""
# 01: Factura de operación interna
# 04: Nota de Crédito referente a una FE
# 06: Nota de Crédito genérica
# 09: Reembolso

n_factura = ""  # numero de factura, debe ser 10N
# punto_facturacion = ""  # Numero de punto de Venta,
# n_sucursal = ""  # Numero de sucursal, 4N
fecha_emision = ""  # AAAA-MM-DDTHH:mm:ss-05:00
naturaleza_op = ""  # 01: Venta, 11: Devolución
tipo_operacion = "1"  # 1: salida o venta
destino = "1"  # 1: Panamá 2: Extranjero
forma_papel = (
    "2"  # 1: Sin generación de CAFE,   2: Cinta de papel,  3: Papel formato carta*
)
entrega = "2"  # 1: Sin generación de CAFE,    2: Entrega papel,    3: electronico
EnvioFE = "1"
progen = "1"
info_interes = ""  # informacion de interes sobbre la factura
tipotranventa = "1"
fechasalida = ""
fechacontingencia = ""  # AAAA-MM-DDTHH:mm:ss-05:00
tipo_sucursal = ""  # no es necesesario 1: Venta al detal,    2: Vental al Mayor
cufepost = ""

#########
### Línea 02 Datos del cliente o recepto
#########
tipo_cliente = "02"
# 01: Contribuyente
# 02: Consumidor final
# 03: Gobierno
# 04: Extranjero

tipo_ruc = "1"  # 1: Natural    # 2: Jurídico
ruc = "0-0-0"  # Contado/Consumidor final es 0-0-0 como defecto
dv = "70"  # Contado/Consumidor final es 70 como defecto
nombre = "Consumidor final"  # Nombre defecto contado
direccion = ""  # Obligatorio si es contribuyente o gobierno
codigo_ubi = ""  # Obligatorio si es contribuyente o gobierno
corregimiento = ""
distrito = ""
provincia = ""
telefono1 = "000-0000"
telefono2 = ""
telefono3 = ""
correo_elec1 = ""
correo_elec2 = ""
correo_elec3 = ""
pais = "PA"
paisdesc = ""  # Obligatorio si es pais ZZ
tipoidextranjero = ""  # Obligatorio si es tipo cliente extranjero
# 01: Pasaporte
# 02: Numero Tributario
# 99: Otro
id_extranjero = ""  # Obligatorio si es tipo cliente extranjero
pais_ext = ""  # Obligatorio si es tipo cliente extranjero

#########
### Línea 04 documento refenciado
#########
ref_tipo_ruc = "1"
ref_ruc = "0-0-0"
ref_dv = "70"
ref_nombre = ""
ref_fecha = ""  # AAAA-MM-DDTHH:mm:ss-05:00

# solo debes reportar uno de los 3
ref_cufe = ""  # N66
ref_factura = ""  # numero del factura papel AN22
ref_facturafiscal = ""  # factura de impresora fiscal AN22 incluye serial tfhkc

### Defecto L01
L01_defecto_p1 = "L01|03|01|"  # llenar numero fac, punto de fact, sucursal, fecha
L01_defecto_p2 = (
    "01|1|1|" + forma_papel + "|" + entrega + "|1|1||1||||||"
)  # desde naturaleza de operacion
L01_defectodev_p1 = "L01|03|06|"
L01_defectodev_p2 = "11|1|1|" + forma_papel + "|" + entrega + "|1|1||1||||||"

def order(order_data, valid, is_refund):
    if not isinstance(order_data, dict):
        raise TypeError("La orden recibida desde Odoo debe ser un objeto")

    order_number = order_data.get("ORDER_NUMBER")
    if order_number in (None, ""):
        raise ValueError("La orden recibida desde Odoo no contiene ORDER_NUMBER")

    LOGGER.info(
        "Inicio de generación TXT | order=%s | valid=%r | refund=%r",
        order_number,
        valid,
        is_refund,
    )
    if not valid:
        LOGGER.warning("Generación omitida porque valid=False | order=%s", order_number)
        return
    contado = False
    contado = any(
        [
            "RAZON_SOCIAL" not in order_data,
            order_data["RAZON_SOCIAL"] == False,
            isinstance(order_data.get("RAZON_SOCIAL", ""), str)
            and order_data["RAZON_SOCIAL"].lower() in ["contado", "consumidor final"],
            order_data.get("TIPO_CLIENTE", "02") == "02",
        ]
    )

    n_factura = order_data["ORDER_NUMBER"]
    current_datetime = datetime.datetime.now()
    fecha_emision = current_datetime.strftime("%Y-%m-%dT%H:%M:%S-05:00")
    limit_emision = current_datetime + timedelta(hours=24)

    # if(order_data['ORDERLINES'][-1]['PRODUCT_NAME'] == "Puntos"):
    #     print("****************************************************************************************")
    #     print(">>>>>>>>>>>>>>>>>>>>>>> Seccion para desarrollo de puntos de fidelizacion: ")
    #     print(json.dumps(order_data['ORDERLINES'][-1], indent=2, sort_keys=True))
    #     # Si solo hay un producto que hay que aplicarle descuento, que lo reste de una vez
    #     if(len(order_data['ORDERLINES']) - 1  == 1):
    #         print("Solo hay que darle Decuento de Puntos a un solo Producto")
    #         order_data['ORDERLINES'][0]['PRICE'] = order_data['ORDERLINES'][0]['PRICE'] - order_data['ORDERLINES'][-1]["PRICE"]
    #         order_data['ORDERLINES'] = [item for item in order_data['ORDERLINES'] if item["PRODUCT_NAME"] != "Puntos"]
    #     # Si hay mas de un producto se hace lo siguiente
    #     else:
    #         print("*********************** Hay que darle descuentos a varios productos")
    #         calc_desc = []

    #         print(json.dumps(order_data["ORDERLINES"][0], indent=2, sort_keys=True))

    #         #Asignar un numero random entre 0 y el precio del primer producto
    #         calc_desc.append(random.uniform(0.0, order_data["ORDERLINES"][-1]["PRICE"]))
    #         # Ese numero debe ser menor que el precio del producto actual
    #         iter = 1
    #         while(calc_desc[0] > order_data["ORDERLINES"][0]['PRICE']):
    #             print("Iteracion:", iter)
    #             calc_desc[0] = random.uniform(0.0, calc_desc[0])
    #             iter+=1

    #         print("\n\nBucle:")
    #         for i in range(1,len(order_data["ORDERLINES"]) - 1):
    #             if(order_data["ORDERLINES"][i] == order_data["ORDERLINES"][-2]):
    #                 last_sum = order_data["ORDERLINES"][-1]["PRICE"] - sum(calc_desc)
    #                 calc_desc.append(last_sum)
    #             else:
    #                 calc_desc.append(random.uniform(0.0, order_data["ORDERLINES"][-1]["PRICE"]))
    #                 iter = 1
    #                 while(calc_desc[-1] > order_data["ORDERLINES"][i]['PRICE']):
    #                     print("Iteracion:", iter)
    #                     calc_desc[-1] = random.uniform(0.0, calc_desc[-1])
    #                     iter +=1
                    
    #         print("Lista de Sumas", calc_desc)
    #         print("Suma, debe ser igual al precio del producto descuento: ", sum(calc_desc))

    #         # Cuando llevas ya todo el calculo listo
    #         #  Eliminar el producto punto
    #         order_data['ORDERLINES'] = [item for item in order_data['ORDERLINES'] if item["PRODUCT_NAME"] != "Puntos"]

    #         # Hacer la resta para tener el nuevo precio de cada item:
    #         for i in range(0,len(order_data["ORDERLINES"])):
    #             order_data["ORDERLINES"][i]['PRICE'] = order_data["ORDERLINES"][i]['PRICE'] - calc_desc[i]

    #     print("\n\nAsi es como quedo al final el orderline:")
    #     print(json.dumps(order_data['ORDERLINES'], indent=2, sort_keys=True))
    #     print("****************************************************************************************")
    extranjero = order_data.get("TIPO_CLIENTE") == "04"

    if contado or extranjero:
        # Asegurar que la razón social sea válida
        razon_social = order_data["RAZON_SOCIAL"] if isinstance(order_data.get("RAZON_SOCIAL"), str) else "Consumidor Final"

        # Determinar tipo de cliente
        tipo_cliente = order_data["TIPO_CLIENTE"] if isinstance(order_data.get("TIPO_CLIENTE"), str) else "02"

        # Manejo del RUC y DV
        ruc = order_data["RUC"] if isinstance(order_data.get("RUC"), str) and tipo_cliente != "02" else ""
        dv = order_data["DV"] if isinstance(order_data.get("DV"), str) and tipo_cliente != "02" else ""

        # Correo electrónico
        Email = order_data["PARTNER_EMAIL"] if isinstance(order_data.get("PARTNER_EMAIL"), str) else ""

        # Generar la línea de datos
        linea2 = (
            f"L02|{tipo_cliente}|1|{ruc}|{dv}|{razon_social}||||||000-0000||||||PA|||||{Email}|"
        )


        if not is_refund:
            linea1 = (
                L01_defecto_p1
                + n_factura
                + "|"
                + punto_facturacion
                + "|"
                + n_sucursal
                + "|"
                + fecha_emision
                + "|"
                + L01_defecto_p2
            )
        elif is_refund:
            linea1 = (
                L01_defectodev_p1
                + n_factura
                + "|"
                + punto_facturacion
                + "|"
                + n_sucursal
                + "|"
                + fecha_emision
                + "|"
                + L01_defectodev_p2
            )
    else:
      
        linea2 = (
            "L02|"
            + str(order_data["TIPO_CLIENTE"])
            + "|"
            + str(order_data["tipo_receptor"]) # se agrego el tipo_receptor enviado desde el addons.
            + "|"
            +order_data["RUC"]
            + "|"
            + (((order_data["DV"]).zfill(2)) or False )
            + "|"
            + order_data["RAZON_SOCIAL"]
            + "|"
            + order_data["DIRECCION"]
            + "|"
            + order_data["COD_UBI"]
            + "|"
            + order_data["CORREGIMIENTO"]
            + "|"
            + order_data["DISTRITO"]
            + "|"
            + order_data["PROVINCIA"]
            + "|"
            + "000-0000||||||PA|||||"
            + "|"
            + str(order_data["PARTNER_EMAIL"]) # Correo Cliente Contribuyente o gobierno
            + "|"
        )
        if not is_refund:
            linea1 = (
                "L01|01|01|"
                + n_factura
                + "|"
                + punto_facturacion
                + "|"
                + n_sucursal
                + "|"
                + fecha_emision
                + "|"
                + L01_defecto_p2
            )
        elif is_refund:
            linea1 = (
                "L01|01|09|" #se cambio de nota credito generico 06 a Rembolso 09.
                + n_factura
                + "|"
                + punto_facturacion
                + "|"
                + n_sucursal
                + "|"
                + fecha_emision
                + "|"
                + L01_defectodev_p2
            )

    # Form L06 Venta
    linea6 = ""
    tax_commands = {0: "00", 7: "01", 10: "02", 15: "03"}
    TotNeto = Decimal("0.00")
    TotITBMS = Decimal("0.00")
    TotISC = Decimal("0.00")
    TotDesc = Decimal("0.00")
    TotAcar = Decimal("0.00")
    TotSeg = Decimal("0.00")
    legacy_line_subtotals = 0
    legacy_tax_amounts = 0
    legacy_line_totals = 0
    # NroItems = 0
    
    for order in order_data["ORDERLINES"]:
        CodProd = "ID" + str(order["ITEM_ID"]) + "|"  # Puede ser código de barras o interno
        DescProd = clean_text_field(order["PRODUCT_NAME"]) + "|"  # Descripción
        CantCodInt = str(order["QUANTITY"]) + "|"  # Cantidad del ítem
        PrUnit = str(order["PRICE"]) + "|"  # Precio unitario extraído desde el frontend

        # DISCOUNT alimenta dPrUnitDesc y representa un importe por unidad, no
        # un porcentaje. Detenemos aquí el caso inequívoco que la DGI rechaza
        # cuando el descuento supera el precio unitario.
        raw_unit_discount = order.get("DISCOUNT", 0)
        unit_price = to_decimal(order["PRICE"], "PRICE")
        unit_discount = to_decimal(raw_unit_discount, "DISCOUNT")
        if unit_discount > unit_price:
            raise ValueError(
                f"DISCOUNT ({unit_discount}) es mayor que PRICE ({unit_price}) "
                f"en '{order.get('PRODUCT_NAME')}'. DISCOUNT espera un importe "
                "por unidad, no un porcentaje; la DGI rechazaría el documento."
            )
        PrUnitDesc = str(raw_unit_discount) + "|"

        # Las versiones antiguas de Odoo no enviaban TOTAL_WITH_TAX. Conservamos
        # el total proporcionado cuando existe y, si falta, lo reconstruimos con
        # los mismos importes redondeados que se escribirán en L06 y L07.
        raw_line_subtotal = order.get("TOTAL_WITH_DISCOUNT")
        if (
            raw_line_subtotal is None
            or raw_line_subtotal is False
            or raw_line_subtotal == ""
        ):
            quantity = to_decimal(order["QUANTITY"], "QUANTITY")
            line_subtotal = to_money(
                (unit_price - unit_discount) * quantity,
                "TOTAL_WITH_DISCOUNT calculado",
            )
            legacy_line_subtotals += 1
        else:
            line_subtotal = to_money(
                raw_line_subtotal,
                "TOTAL_WITH_DISCOUNT",
            )

        # El precio total después del descuento
        PrItem = money_text(line_subtotal) + "|"

        PrAcarItem = "|"
        PrSegItem = "|"

        # Redondeamos una sola vez el impuesto del ítem. El mismo importe debe
        # escribirse en L06 y acumularse en L07 para que dTotITBMS coincida
        # exactamente con la suma de todos los dValITBMS.
        tax_rate = to_decimal(order.get("TAX", 0), "TAX")
        TasaITBMS = tax_commands.get(tax_rate, "00")  # Obtenemos el código de impuesto
        raw_tax_amount = order.get("TAX_AMOUNT")
        if raw_tax_amount is None or raw_tax_amount is False or raw_tax_amount == "":
            if tax_rate not in (Decimal("0"), Decimal("7"), Decimal("10"), Decimal("15")):
                raise ValueError(
                    f"TAX no soportado para calcular TAX_AMOUNT: {tax_rate}"
                )
            line_itbms = to_money(
                line_subtotal * tax_rate / Decimal("100"),
                "TAX_AMOUNT calculado",
            )
            legacy_tax_amounts += 1
        else:
            line_itbms = to_money(raw_tax_amount, "TAX_AMOUNT")
        ValITBMS = money_text(line_itbms) + "|"
        #print("Esto es ValITBMS", ValITBMS)

        raw_line_total = order.get("TOTAL_WITH_TAX")
        if raw_line_total is None or raw_line_total is False or raw_line_total == "":
            line_total = line_subtotal + line_itbms
            legacy_line_totals += 1
        else:
            line_total = to_money(raw_line_total, "TOTAL_WITH_TAX")

        TasaISC = "|"
        ValISC = "|"
        CodOTI = "|"
        ValOTI = "|"
        ValTotItem = money_text(line_total) + "|"
        # Versiones anteriores del módulo Odoo no enviaban UNIT_MEASURE.
        # Para productos por unidad, Factura Fácil acepta "und" como valor por defecto.
        unit_measure = clean_text_field(
            order.get("UNIT_MEASURE")
            or order.get("UOM")
            or order.get("UNIT")
            or "und"
        ) or "und"
        Unidad = unit_measure + "|"
        InfEmFE = "|"  # Información de interés, comentario

        FechaFab = "|"
        FechaCad = "|"

        CodCPBSabr = "|"
        # False/None no deben escribirse literalmente en el TXT.
        CodCPBScmp = clean_text_field(order.get("CPBS_CODE")) + "|"
        UnidadCPBS = unit_measure + "|"
        GTINCom = "|"
        CantGTINCom = "|"
        GTINInv = "|"
        CantComInven = "|"
        Datos_Vehículos = "|"

        # Construir la línea 06 con los datos directamente obtenidos del frontend
        linea6 += (
            "\nL06|"
            + CodProd
            + DescProd
            + CantCodInt
            + PrUnit
            + PrUnitDesc
            + PrItem
            + PrAcarItem
            + PrSegItem
            + TasaITBMS
            + "|"
            + ValITBMS
            + TasaISC
            + ValISC
            + CodOTI
            + ValOTI
            + ValTotItem
            + Unidad
            + InfEmFE
            + FechaFab
            + FechaCad
            + CodCPBSabr
            + "|"              #CodCPBScmp
            + UnidadCPBS
            + GTINCom
            + CantGTINCom
            + GTINInv
            + CantComInven
            + Datos_Vehículos
            + "|"
            + CodCPBScmp
        )

        # Sumamos los totales enviados desde el frontend
        TotNeto += line_subtotal
        TotITBMS += line_itbms

    if legacy_line_subtotals or legacy_tax_amounts or legacy_line_totals:
        LOGGER.info(
            "Compatibilidad Odoo anterior: importes calculados por el conector "
            "| order=%s | subtotales=%s | impuestos=%s | totales=%s",
            n_factura,
            legacy_line_subtotals,
            legacy_tax_amounts,
            legacy_line_totals,
        )

    # Calcular los totales para el archivo
    TotISC = "|"  # Aún no implementado
    TotGravado = TotITBMS  # + TotISC + ValOTI
    VTotItem = TotNeto + TotITBMS
    TotDesc = "|"  # Descuento global y otros aún no implementado
    TotAcar = "|"  # Aún no implementado
    TotSeg = "|"  # Aún no implementado
    VTot = VTotItem  # - TotDesc + TotAcar + TotSeg

    # El vuelto y los pagos deben cumplir:
    # suma(L08.dVlrCuota) = dTotRec = dVTot + dVuelto.
    # El conector anterior copiaba PAYMENTLINES y CHANGE sin conciliarlos,
    # generando los errores DGI 2510 y "vuelto entregado inválido".
    Vuelto2 = max(to_money(order_data.get("CHANGE", 0), "CHANGE"), Decimal("0.00"))
    TotRec, linea8 = payment(
        order_data.get("PAYMENTLINES", []),
        invoice_total=VTot,
        change=Vuelto2,
    )

    PzPag = str(1)
    CodRetenc = "|"  # Aun no implementado
    ValRetenc = "|"  # Aun no implementado
    raw_item_count = order_data.get("TOTAL_QUANTITY")
    if raw_item_count is None or raw_item_count is False or raw_item_count == "":
        # En el formato actual TOTAL_QUANTITY representa la cantidad de líneas,
        # no la suma de unidades. Las versiones antiguas no enviaban este campo.
        NroItems = len(order_data["ORDERLINES"])
        LOGGER.info(
            "Compatibilidad Odoo anterior: TOTAL_QUANTITY calculado por el conector "
            "| order=%s | líneas=%s",
            n_factura,
            NroItems,
        )
    else:
        NroItems = int(raw_item_count)

    # Línea 07: Totales y subtotales
    linea7 = "\nL07|" + (
        money_text(TotNeto)  # Total Neto
        + "|"
        + money_text(TotITBMS)  # Total ITBMS
        + "|"
        + TotISC  # Aún no implementado
        + money_text(TotGravado)  # Gravado
        + "|"
        + money_text(VTotItem)  # Total con impuestos
        + "|"
        + TotDesc  # Descuento global
        + TotAcar  # Acarreos
        + TotSeg  # Seguro
        + money_text(VTot)  # Valor total
        + "|"
        + money_text(TotRec)  # Lo que el cliente pagó
        + "|"
        + money_text(Vuelto2)  # Vuelto
        + "|"
        + PzPag
        + "|"
        + str(NroItems)  # Número de ítems
        + "|"
        + CodRetenc
        + ValRetenc
    )
    
    if order_data["ORDERLINES"][0]["PRODUCT_NAME"] != "Abono":
        AGENT_DIR.mkdir(parents=True, exist_ok=True)
        direccion = AGENT_DIR / f"{str(n_factura)}.txt"
        with direccion.open("w", encoding="cp1252", newline="") as file:
            file.write(
                linea1 + "\n" +
                linea2 +
                linea6 +
                linea7 +
                linea8
            )
        LOGGER.info(
            "Documento TXT creado | order=%s | ruta=%s | bytes=%s",
            n_factura,
            direccion,
            direccion.stat().st_size,
        )
        return direccion

    LOGGER.info("Orden de tipo Abono: no se genera TXT fiscal | order=%s", n_factura)
    return None



def payment(paylines, invoice_total, change):
    """Arma L08 y concilia los importes recibidos con total + vuelto.

    Reglas aplicadas:
      * dTotRec = suma de todos los dVlrCuota.
      * dVuelto = dTotRec - dVTot.
      * Si Odoo manda el pago neto en vez del efectivo entregado, la diferencia
        se agrega a la línea de efectivo.
      * Si existe una sola forma de pago, también se puede ajustar con seguridad.
      * En pagos mixtos sin efectivo, solo se corrigen diferencias de redondeo
        de hasta dos centavos; una diferencia mayor detiene el documento.
    """
    invoice_total = to_money(invoice_total, "total de factura")
    change = max(to_money(change, "vuelto"), Decimal("0.00"))
    expected_received = (invoice_total + change).quantize(
        MONEY_QUANT, rounding=ROUND_HALF_UP
    )

    normalized = []
    for raw_payment in paylines or []:
        pay_name = clean_text_field(raw_payment.get("PAY_NAME") or "Cash") or "Cash"
        pay_amount = to_money(raw_payment.get("AMOUNT", 0), f"pago {pay_name}")
        if pay_amount < 0:
            pay_amount = abs(pay_amount)

        explicit_payment = resolve_explicit_payment_code(
            raw_payment.get("PAYMENT_CODE"),
            pay_name,
        )
        if explicit_payment is not None:
            # PAYMENT_CODE pertenece a la integración de Odoo. Antes de
            # escribir L08 se traduce al catálogo que acepta FEL/Agente.
            payid, description = explicit_payment
        else:
            matching_tuple = next(
                (
                    method_tuple
                    for method_tuple in metodo_pago
                    if method_tuple[0].lower() == pay_name.lower()
                ),
                None,
            )
            if matching_tuple:
                payid = matching_tuple[1]
                description = ""
            else:
                payid = "99"
                description = fel_payment_description(pay_name)

        normalized.append(
            {
                "name": pay_name,
                "payid": payid,
                "description": description,
                "amount": pay_amount,
            }
        )

    if not normalized and expected_received > 0:
        normalized.append(
            {
                "name": "Cash",
                "payid": "02",
                "description": "",
                "amount": expected_received,
            }
        )

    current_received = sum(
        (item["amount"] for item in normalized), Decimal("0.00")
    ).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    difference = (expected_received - current_received).quantize(
        MONEY_QUANT, rounding=ROUND_HALF_UP
    )

    if difference != Decimal("0.00") and normalized:
        cash_index = next(
            (index for index, item in enumerate(normalized) if item["payid"] == "02"),
            None,
        )

        if cash_index is not None:
            target_index = cash_index
        elif len(normalized) == 1:
            target_index = 0
        elif abs(difference) <= Decimal("0.02"):
            # Diferencia mínima causada por redondeos en pagos mixtos.
            target_index = len(normalized) - 1
        else:
            raise ValueError(
                "Pagos FEL inconsistentes: total factura="
                f"{money_text(invoice_total)}, vuelto={money_text(change)}, "
                f"recibido={money_text(current_received)}, "
                f"esperado={money_text(expected_received)}. "
                "No existe una línea de efectivo que pueda conciliarse."
            )

        corrected_amount = normalized[target_index]["amount"] + difference
        if corrected_amount < 0:
            raise ValueError(
                "La corrección de pagos FEL produciría un importe negativo en "
                f"{normalized[target_index]['name']}."
            )
        normalized[target_index]["amount"] = corrected_amount.quantize(
            MONEY_QUANT, rounding=ROUND_HALF_UP
        )

    total_received = sum(
        (item["amount"] for item in normalized), Decimal("0.00")
    ).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)

    calculated_change = (total_received - invoice_total).quantize(
        MONEY_QUANT, rounding=ROUND_HALF_UP
    )
    if total_received != expected_received or calculated_change != change:
        raise ValueError(
            "No fue posible conciliar los pagos FEL: "
            f"total={money_text(invoice_total)}, "
            f"recibido={money_text(total_received)}, "
            f"vuelto={money_text(change)}."
        )

    linea8 = ""
    for item in normalized:
        forma_pago_des = item["description"] + "|" if item["description"] else "|"
        linea8 += (
            "\nL08|"
            + item["payid"]
            + "|"
            + money_text(item["amount"])
            + "|"
            + forma_pago_des
        )

    LOGGER.info(
        "Pagos FEL conciliados | resumen=%r",
        {
            "total": money_text(invoice_total),
            "vuelto": money_text(change),
            "recibido": money_text(total_received),
            "pagos": [
                {
                    "metodo": item["name"],
                    "codigo": item["payid"],
                    "importe": money_text(item["amount"]),
                }
                for item in normalized
            ],
        },
    )
    return total_received, linea8

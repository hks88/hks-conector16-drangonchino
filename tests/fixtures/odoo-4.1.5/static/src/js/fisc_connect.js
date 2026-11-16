/** @odoo-module **/
import framework from 'web.framework';
const PaymentScreen = require("point_of_sale.PaymentScreen");
const Registries = require("point_of_sale.Registries");
const { PROTOCOL_VERSION, fiscalMode, legacyNumber, requestLegacy, fiscalIdentity, newOrderUid, requestFiscal } = require("hks_pos_offline_FF_gobierno.fiscal_protocol");
var { Order } = require("point_of_sale.models");
var { Gui } = require("point_of_sale.Gui");
var core = require("web.core");
var _t = core._t;

console.log('%c********* MODULO FEL FF CARGADO *********', 'color: green; font-weight: bold;');
console.log("%c******Version 4.1.5 MIGRACION POR TPV (5.1.9 heredado / 5.2.8 local)******",'color: green; font-weight: bold;');

////////////////
///// Server Connect    
////////////////
let connected = false;

const socket = io("http://localhost:3222");

const connectSocket = () => {
    socket.on("connect", handleSocketConnect);
    socket.on("disconnect", reconnectSocket);
};

const reconnectSocket = () => {
    connected = false;
    setTimeout(() => {
        console.log("%c********* Disconnected from Server", 'color: red; font-weight: bold;');
        console.log("%c********* Reconnecting...", 'color: blue; font-weight: bold;');
    }, 500);
};

const handleSocketConnect = () => {
    console.log("%c ****** Connected to Server *******",'color: green; font-weight: bold;');
    connected = true;
};

connectSocket();

let valid = true;
let is_refund = false;

////////////////
/////  Injecta Datos
////////////////

const hks_fel_data = (Order) =>
    class hks_fel_data extends Order {
        constructor(...args) {
            super(...args);
            this.hks_fiscal_mode = this.hks_fiscal_mode || fiscalMode(this.pos);
            if (!this.hks_fiscal_uid && !this.hks_legacy_pending) this.hks_fiscal_uid = newOrderUid();
        }
        fel_result(data) {
            this.tiempo_limite_emision = data.fl_emision;
            return;
        }
        export_for_printing() {
            var result = super.export_for_printing(...arguments);
            result.cufe = this.cufe || null;
            result.hks_fiscal_number = this.hks_fiscal_number || null;
            result.fel_qr = this.fel_qr || null;
            if (this.fel_qr) {
                const codeWriter = new window.ZXing.BrowserQRCodeSvgWriter();
                let qr_code_svg = new XMLSerializer().serializeToString(
                    codeWriter.write(this.fel_qr, 200, 200)
                ); // Miguel tamano de QR
                var qrcoded =
                    "data:image/svg+xml;base64," + window.btoa(qr_code_svg);
                result.pos_qr_code = qrcoded || null;
                result.tiempo_limite_emision = this.tiempo_limite_emision;
            }
            return result;
        }
        init_from_JSON(json) {
            super.init_from_JSON(...arguments);
            this.cufe = json.cufe || null;
            this.fel_qr = json.fel_qr || null;
            this.hks_fiscal_uid = json.hks_fiscal_uid || null;
            this.hks_fiscal_number = json.hks_fiscal_number || null;
            this.hks_fiscal_request = json.hks_fiscal_request || null;
            this.hks_fiscal_state = json.hks_fiscal_state || null;
            this.hks_fiscal_mode = json.hks_fiscal_mode || (json.hks_fiscal_protocol === PROTOCOL_VERSION ? 'local' : 'odoo');
            this.hks_legacy_request = json.hks_legacy_request || null;
            this.hks_legacy_state = json.hks_legacy_state || null;
            this.hks_legacy_refund = json.hks_legacy_refund || false;
            this.hks_legacy_pending = Boolean(json.hks_legacy_pending) ||
                (!json.hks_fiscal_mode && json.hks_fiscal_protocol !== PROTOCOL_VERSION);
        }

        export_as_JSON() {
            var result = super.export_as_JSON(...arguments);
            result.cufe = this.cufe || null;
            result.fel_qr = this.fel_qr || null;
            result.hks_fiscal_uid = this.hks_fiscal_uid || null;
            result.hks_fiscal_number = this.hks_fiscal_number || null;
            result.hks_fiscal_request = this.hks_fiscal_request || null;
            result.hks_fiscal_state = this.hks_fiscal_state || null;
            result.hks_fiscal_mode = this.hks_fiscal_mode || fiscalMode(this.pos);
            result.hks_legacy_pending = Boolean(this.hks_legacy_pending);
            result.hks_legacy_request = this.hks_legacy_request || null;
            result.hks_legacy_state = this.hks_legacy_state || null;
            result.hks_legacy_refund = Boolean(this.hks_legacy_refund);
            result.hks_fiscal_protocol = result.hks_fiscal_mode === 'local' && !this.hks_legacy_pending ? PROTOCOL_VERSION : 0;
            return result;
        }
    };

Registries.Model.extend(Order, hks_fel_data);

////////////////
/////  Loader del paymentScreen
////////////////

function createLoader() {
    // Esperar a que el DOM esté listo
    document.addEventListener("DOMContentLoaded", function () {
        if (!document.getElementById('loader-overlay')) {
            const loaderOverlay = document.createElement('div');
            loaderOverlay.id = 'loader-overlay';
            loaderOverlay.style.position = 'fixed';
            loaderOverlay.style.top = '0';
            loaderOverlay.style.left = '0';
            loaderOverlay.style.width = '100%';
            loaderOverlay.style.height = '100%';
            loaderOverlay.style.backgroundColor = 'rgba(0, 0, 0, 0.5)';
            loaderOverlay.style.zIndex = '9999';
            loaderOverlay.style.display = 'none'; // Inicialmente oculto

            const loader = document.createElement('div');
            loader.id = 'loader';
            loader.style.position = 'absolute';
            loader.style.top = '50%';
            loader.style.left = '50%';
            loader.style.transform = 'translate(-50%, -50%)';
            loader.style.width = '80px';
            loader.style.height = '80px';
            loader.style.border = '8px solid #f3f3f3';
            loader.style.borderRadius = '50%';
            loader.style.borderTop = '8px solid #3498db';
            loader.style.animation = 'spin 1s linear infinite';

            // Añadir el loader al overlay
            loaderOverlay.appendChild(loader);

            // Añadir el overlay al body cuando el DOM esté completamente cargado
            document.body.appendChild(loaderOverlay);
        }
    });
}

function showLoader() {
    const loaderOverlay = document.getElementById('loader-overlay');
    if (loaderOverlay) {
        loaderOverlay.style.display = 'block'; // Mostrar el loader
    }
}


function hideLoader() {
    const loaderOverlay = document.getElementById('loader-overlay');
    if (loaderOverlay) {
        loaderOverlay.style.display = 'none'; // Ocultar el loader
    }
}


createLoader();

////////////////
/////  Detecta el Validacion
////////////////

const listentopay = (PaymentScreen) =>
    class extends PaymentScreen {
        setup() {
            super.setup();
            this.state = {
                is_email_sent: false,
            };
            this.charge();
        }

        ////////////////
        /////  Correo Electronico del cliente
        ////////////////
        toggleEmailState() {
            this.state.is_email_sent = !this.state.is_email_sent;
        
            let PARTNER_EMAIL = null;
            
            if (this.state.is_email_sent) {
                PARTNER_EMAIL = this.currentOrder.partner && this.currentOrder.partner.email ? this.currentOrder.partner.email : null;
                console.log("El correo está marcado para ser añadido en el order_data:", PARTNER_EMAIL);
            } else {
                PARTNER_EMAIL = null;
                console.log("El correo NO será añadido en el order_data.");
            }
            this.PARTNER_EMAIL = PARTNER_EMAIL; 
            this.render();
        }

        async charge() {
            console.log("- Current Order:", this.currentOrder);
            console.log("- Orderlines:", this.currentOrder.orderlines);
            console.log("- Paymentlines:",this.currentOrder.paymentlines);
            console.log("- Partner:", this.currentOrder.partner);
            console.log("- Change:", this.currentOrder.get_change());
        }
        //////////////////////
        ///////// Validar Datos Necesarios * Nota Credito
        /////////////////////

        // Filtra ordenes para identificar si hay devolucion
        async _isOrderValid(isForceValidate) {
            is_refund = false;
            let sale = false;

            if (this.currentOrder.orderlines.length == 0) {
                hideLoader();
                await this.showPopup("ErrorPopup", {
                    title: this.env._t("Venta sin producto/ 没有产品的销售"),
                    body: this.env._t("No se puede hacer venta sin producto!/ 无法进行没有产品的销售！"),
                });
                return false;
            }

            for (const x of this.currentOrder.orderlines) {
                if (x.refunded_orderline_id) {
                    is_refund = true;
                    if (sale) break;
                }
                if (!x.refunded_orderline_id) {
                    sale = true;
                    if (is_refund) break;
                }
            }
            if (sale & is_refund) {
                hideLoader();
                await this.showPopup("ErrorPopup", {
                    title: this.env._t(
                        "No se puede hacer Nota de Credito Con Venta"
                    ),
                    body: this.env._t(
                        "Porfavor primero hacer nota de credito luego haz la venta."
                    ),
                });
                return false;
            }
            if (!connected) {
                hideLoader();
                await this.showPopup("ErrorPopup", {
                    title: this.env._t("无法连接到FEL连接器"),
                    body: this.env._t("Revise si el conector está activado o si el CORS del navegador está bloqueando la conexión/ 请检查连接器是否已激活，或浏览器的CORS是否阻止了连接"),
                });
                return false;
            }
            let partner = this.currentOrder.get_partner() || false;

            if (
                partner &&
                partner.tipo_cliente !== "02" &&
                partner.tipo_cliente !== "04"
            ){
                if (
                    !partner.ruc ||
                    partner.dv === undefined ||
                    partner.dv === null || 
                    !partner.vat
                ) 
                {
                    hideLoader();
                    this.showPopup("ErrorPopup", {
                        title: this.env._t("RUC y DV necesarios/ 需要RUC和DV"),
                        body: this.env._t("Clientes Contribuyentes y Gobiernos necesitan RUC y DV/ 纳税客户和政府需要RUC和DV"),
                    });
                    return false;
                }
                
                if (!partner.tipo_receptor) {
                    hideLoader();
                    this.showPopup("ErrorPopup", {
                        title: this.env._t("Tipo de Receptor necesario/ 需要接收者类型"),
                        body: this.env._t("Es necesario validar el RUC para completar la información del tipo de receptor. Por favor, presione el botón de validar RUC/ 需要验证RUC以完成接收者类型的信息。请按下验证RUC按钮"),
                    });
                    return false;
                }
            }


            // Nueva validación para tipo de cliente "Gobierno" (03)
            if (partner && partner.tipo_cliente === "03") {
        
                let orderLines = this.currentOrder.get_orderlines();
                
                for (let line of orderLines) {
                    let product = line.get_product();

                    if (!product.segmento_code) {
                        hideLoader();
                        this.showPopup("ErrorPopup", {
                            title: this.env._t("Segmento necesario/ 需要的类别"),
                            body: this.env._t("Los productos en órdenes de clientes tipo Gobierno requieren un código de segmento/ 政府类客户订单中的产品需要一个类别代码"),
                        });
                        return false; // Detiene el proceso si falta el "segmento_code"
                    }
                }
            }
            hideLoader();
            return super._isOrderValid(isForceValidate);
        }

        async _finalizeValidation() {
            if (this.hksSubmitting) return;
            this.hksSubmitting = true;
            showLoader();
            framework.blockUI();
            try {
                await this.send_order_fel();
            } catch (error) {
                framework.unblockUI();
                hideLoader();
                this.hksSubmitting = false;
                await this.showPopup("ErrorPopup", {
                    title: this.env._t("Documento fiscal pendiente"),
                    body: error.message || String(error),
                });
                return;
            }
            framework.unblockUI();
            try {
                // Solo después de la respuesta local del Agente FF se finaliza
                // el pedido; el flujo estándar conserva la sincronización offline.
                await super._finalizeValidation();
            } finally {
                hideLoader();
                this.hksSubmitting = false;
            }
        }

        async send_order_fel() {
            const order = this.currentOrder;
            const payload = this._build_fiscal_payload();
            if (fiscalMode(order.pos) === 'odoo') {
                return requestLegacy(socket, order, payload, order.orderlines.some(line => Boolean(line.refunded_orderline_id)));
            }
            return requestFiscal(socket, order, payload);
        }

        _build_fiscal_payload() {
            const local = fiscalMode(this.currentOrder.pos) === 'local';
            const identity = local ? fiscalIdentity(this.currentOrder.pos) : null;
            const normalOrderlines = [];
            const sub_debug = false;
            const total_orderlines = this.currentOrder.orderlines.length;
            let abono_count = 0;
        
            this.currentOrder.orderlines.forEach((orderline) => {
                if (sub_debug) {
                    console.log(
                        ">>> refund? :",
                        orderline.product.display_name,
                        orderline.refunded_orderline_id ?? false
                    );
                }
        
                let serial_pack = [];
                if (orderline.pack_lot_lines) {
                    for (const x of orderline.pack_lot_lines) {
                        if (x.lot_name) {
                            serial_pack.push(x.lot_name);
                        }
                    }
                }
        
                // Extraemos los datos directamente de las funciones del modelo Orderline
                const unit_price = orderline.get_unit_price();  // Precio unitario calculado
                const discount_percentage = orderline.get_discount();  // Porcentaje de descuento calculado
                const quantity = Math.abs(orderline.get_quantity());  // Hacemos que la cantidad sea siempre positiva
                
                // Calcular el descuento por unidad
                const discount_per_unit = (unit_price * discount_percentage / 100);
                
                // Calcular el subtotal después del descuento
                const subtotal_after_discount = Math.round((unit_price - (unit_price * discount_percentage / 100)) * quantity * 100) / 100;
        
                // Extraer el subtotal sin impuestos y el total con impuestos
                const subtotal = orderline.get_price_without_tax();  // Subtotal sin impuestos
                const tax_amount = orderline.get_tax();  // Impuesto calculado
                const total_with_tax = orderline.get_price_with_tax();  // Precio con impuestos
        
                // NOTA! esto de tax es por causa de diferencia de versión que encontré
                var x_tax = 0;
                if (orderline.get_tax_percentages) {
                    x_tax = orderline.get_tax_percentages()[0];
                } else if (!this.currentOrder.fiscal_position) {
                    if (
                        orderline.get_applicable_taxes() &&
                        orderline.get_applicable_taxes().length > 0
                    ) {
                        x_tax = orderline.get_applicable_taxes()[0].amount;
                    }
                } else {
                    if (orderline.get_applicable_taxes() > 0) {
                        var target_tax = [7, 10, 15];
                        x_tax = target_tax.reduce(function (prev, curr) {
                            return Math.abs(
                                curr - orderline.get_applicable_taxes().amount
                            ) <
                                Math.abs(
                                    prev - orderline.get_applicable_taxes().amount
                                )
                                ? curr
                                : prev;
                        });
                    }
                }
        
                // Modificación: Si es un reembolso, aseguramos que los valores sean positivos
                const isRefund = orderline.refunded_orderline_id !== null;
        
                // Crear la estructura de los datos de la línea de pedido
                const filteredOrderline = {
                    PRICE: isRefund ? Math.abs(unit_price) : unit_price,  // Si es reembolso, precio positivo
                    PRODUCT_NAME: orderline.product.display_name,
                    QUANTITY: quantity,  // La cantidad ya está en valor absoluto
                    TAX: isRefund ? Math.abs(x_tax) : x_tax,  // Si es reembolso, impuesto positivo
                    TAX_AMOUNT: isRefund ? Math.abs(tax_amount) : tax_amount,  // Impuesto positivo
                    CPBS_CODE: orderline.product.segmento_code,
                    DISCOUNT: isRefund ? Math.abs(discount_per_unit) : discount_per_unit,  // Descuento positivo
                    TOTAL_WITH_DISCOUNT: isRefund ? Math.abs(subtotal_after_discount) : subtotal_after_discount,  // Subtotal después del descuento positivo
                    TOTAL_WITH_TAX: isRefund ? Math.abs(total_with_tax) : total_with_tax,  // Total con impuestos positivo
                    ITEM_ID: local ? orderline.product.id : orderline.id,
                    REFUNDED_ORDERLINE_ID: orderline.refunded_orderline_id,
                    SERIAL: serial_pack.length > 0 ? serial_pack : false,
                };
        
                console.log("Datos finales de la línea extraídos:", filteredOrderline);
                normalOrderlines.push(filteredOrderline);
            });
        
            // Saca datos de pagos
            const FilteredPayment = this.currentOrder.paymentlines.map(
                (paymentlines) => ({
                    PAY_NAME: paymentlines.name,
                    AMOUNT: Math.abs(paymentlines.amount),  // Aseguramos que los pagos siempre sean positivos
                })
            );
            console.log("Datos de pagos extraídos:", FilteredPayment);
        
            // Para los Abonos
            for (let i = 0; i < normalOrderlines.length; i++) {
                normalOrderlines[i].PRODUCT_NAME = (normalOrderlines[i].PRODUCT_NAME === "Down Payment (POS)" || normalOrderlines[i].PRODUCT_NAME === "Pago adelantado (POS)")
                    ? "Abono" : normalOrderlines[i].PRODUCT_NAME;
            }
        
            for (let i = 0; i < normalOrderlines.length; i++) {
                if (normalOrderlines[i].PRODUCT_NAME === "Abono" || normalOrderlines[i].PRODUCT_NAME === "Down Payment (POS)" || 
                    normalOrderlines[i].PRODUCT_NAME === "Pago adelantado (POS)" ) {
                    abono_count += 1;  // Suma +1 por cada producto 'Abono'
                }
            }
        
            let normalOrderlines1 = normalOrderlines;
            let FilteredPayment1 = false;
        
            if (normalOrderlines[0].PRODUCT_NAME != "Abono" && normalOrderlines[normalOrderlines.length - 1].PRODUCT_NAME === "Abono") {
                let acc_orderline = 0;
                let acc_paymentline = 0;
                for (let i = 0; i < normalOrderlines.length; i++) {
                    if (normalOrderlines[i].PRODUCT_NAME == "Abono") {
                        acc_orderline = normalOrderlines[i].PRICE + acc_orderline;
                    }
                }
                for (let i = 0; i < FilteredPayment.length; i++) {
                    acc_paymentline = FilteredPayment[i].AMOUNT + acc_paymentline;
                }
                let acc_tot = acc_orderline + acc_paymentline;
                FilteredPayment1 = [{ PAY_NAME: "Cash", AMOUNT: acc_tot }];
                normalOrderlines1 = normalOrderlines.filter((value) => value.PRODUCT_NAME != "Abono");
            }
        
            let razon_social;
            let partner = this.currentOrder.partner;
            if (partner && partner.tipo_cliente !== "02" && partner.tipo_cliente !== "04") {
                razon_social = partner.vat;
            } else {
                if (partner === null) {
                    razon_social = false;
                } else {
                    razon_social = partner.name;
                }
            }
        
            const change_amount = this.currentOrder.get_change();
            const Orderlines_with_abono = total_orderlines - abono_count;
        
            const order_data = {
                RAZON_SOCIAL: razon_social || false,
                RUC: this.currentOrder.partner && this.currentOrder.partner.ruc || false,
                DV: this.currentOrder.partner && this.currentOrder.partner.dv || false,
                TIPO_CLIENTE: this.currentOrder.partner && this.currentOrder.partner.tipo_cliente || false,
                tipo_receptor: this.currentOrder.partner && this.currentOrder.partner.tipo_receptor || false,
                DIRECCION: this.currentOrder.partner && this.currentOrder.partner.street,
                COUNTRY: this.currentOrder.partner && this.currentOrder.partner.country_id[1],
                PROVINCIA: this.currentOrder.partner && this.currentOrder.partner.state_id[1],
                DISTRITO: this.currentOrder.partner && this.currentOrder.partner.distrito_id[1],
                CORREGIMIENTO: this.currentOrder.partner && this.currentOrder.partner.corregimiento_id[1],
                COD_UBI: this.currentOrder.partner && this.currentOrder.partner.codigo_ubicacion,
                PARTNER_EMAIL: this.currentOrder.hks_fiscal_request ? this.currentOrder.hks_fiscal_request.PARTNER_EMAIL :
                    (this.currentOrder.hks_legacy_request ? this.currentOrder.hks_legacy_request.PARTNER_EMAIL : (this.PARTNER_EMAIL || false)),
                ...(local ? {
                    FISCAL_PROTOCOL: PROTOCOL_VERSION,
                    FISCAL_IDENTITY: identity,
                    ORDER_UID: this.currentOrder.hks_fiscal_uid,
                    IS_REFUND: this.currentOrder.orderlines.some((line) => Boolean(line.refunded_orderline_id)),
                } : {ORDER_NUMBER: legacyNumber(this.currentOrder)}),
                ORDERLINES: normalOrderlines1,
                PAYMENTLINES: FilteredPayment1 || FilteredPayment,
                CHANGE: change_amount || 0,
                TOTAL_QUANTITY: Orderlines_with_abono || 0, // Cantidad total de productos
            };
            return order_data;
        }
        
    };
Registries.Component.extend(PaymentScreen, listentopay);

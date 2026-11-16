odoo.define('hks_pos_offline_FF_gobierno.fiscal_protocol', function () {
    'use strict';
    const PROTOCOL_VERSION = 2;
    // Factura Fácil puede tardar más de 30 segundos en producción. El conector
    // conserva la solicitud y su número, pero Odoo debe esperar el ACK tardío
    // para evitar obligar al cajero a consultar de nuevo una venta ya emitida.
    const FISCAL_ACK_TIMEOUT_MS = 60000;

    function fiscalMode(pos) {
        const config = pos && pos.config;
        // Identidad sin selector corresponde al loader 4.1.1: sigue siendo local.
        const mode = config && (config.hks_ff_mode || (config.hks_ff_identity ? 'local' : 'odoo'));
        if (!['odoo', 'local'].includes(mode)) throw new Error('Modo fiscal del TPV desconocido. Recargue el POS sin borrar los pedidos.');
        return mode;
    }

    function assertOrderMode(order, expected) {
        if (fiscalMode(order.pos) !== expected || order.hks_fiscal_mode !== expected) {
            throw new Error('El modo fiscal de este pedido no coincide con el TPV. No se convertirá ni se renumerará un pedido durante la migración.');
        }
    }

    function legacyNumber(order) {
        if (order.hks_legacy_request) return order.hks_legacy_request.ORDER_NUMBER;
        const session = order.pos.pos_session;
        // Fórmula exacta del módulo 4.0: 4 + 2 + 4. Nunca cortar dígitos.
        const number = String(session.id).padStart(4, '0') +
            String(session.login_number).padStart(2, '0') + String(order.sequence_number).padStart(4, '0');
        if (!/^[0-9]{10}$/.test(number) || number === '0000000000') {
            throw new Error('La numeración antigua supera 10 dígitos o es inválida. Active el contador local en esta PC y TPV; no se enviará este número a FF.');
        }
        return number;
    }

    async function requestLegacy(socket, order, payload, refund, timeoutMs = FISCAL_ACK_TIMEOUT_MS) {
        assertOrderMode(order, 'odoo');
        if (order.hks_legacy_pending || order.hks_fiscal_request || (order.cufe && !order.hks_legacy_request)) {
            throw new Error('Pedido anterior al cambio de numeración o con estado desconocido. Concilie con FF; no se recuperará automáticamente.');
        }
        if (!/^[0-9]{10}$/.test(payload.ORDER_NUMBER) || payload.ORDER_NUMBER === '0000000000' ||
            ['FISCAL_PROTOCOL', 'FISCAL_IDENTITY', 'ORDER_UID', 'FISCAL_NUMBER'].some(key => key in payload)) {
            throw new Error('Solicitud incompatible con el protocolo antiguo de 5.1.9.');
        }
        const clean = JSON.parse(JSON.stringify(payload));
        if (order.hks_legacy_request && (canonical(order.hks_legacy_request) !== canonical(clean) || order.hks_legacy_refund !== refund)) {
            throw new Error('La venta cambió después de enviarla a FF; no se reenviará.');
        }
        if (order.hks_legacy_state === 'processed') return {procesado:true, replayed:true};
        if (order.hks_legacy_state) {
            throw new Error('Este pedido ya fue enviado por el protocolo antiguo. Consulte su estado en FF antes de reintentar; 5.1.9 no garantiza un reenvío sin duplicados.');
        }
        order.hks_legacy_request = clean;
        order.hks_legacy_refund = refund;
        order.hks_legacy_state = 'sent';
        try { order.save_to_db(); } catch (error) { order.hks_legacy_state = null; throw error; }
        return new Promise((resolve, reject) => {
            let settled = false;
            const timer = setTimeout(() => {
                settled = true;
                reject(new Error('5.1.9 no confirmó el documento. Conserve este pedido y consulte FF; no se enviará otra vez automáticamente.'));
            }, timeoutMs);
            const abono = clean.ORDERLINES[0].PRODUCT_NAME === 'Abono';
            // Argumentos de 5.1.9, con ACK de esta solicitud. No asociar eventos
            // fel_response_event globales a una venta distinta.
            socket.emit('new_order', clean, true, refund, true, !abono, (response) => {
                if (settled) return;
                settled = true;
                clearTimeout(timer);
                try {
                    if (abono && response && response.success === true && response.file === null) {
                        response = {...response, procesado:true, sin_documento:true};
                    }
                    if (!response || !response.procesado || (!response.sin_documento &&
                        (!response.contenido || !response.contenido.identificador))) {
                        throw new Error(response && (response.mensaje || response.error) || 'Respuesta antigua incompleta. Conserve el pedido y consulte FF.');
                    }
                    if (!response.sin_documento) {
                        order.cufe = response.contenido.identificador;
                        order.fel_qr = response.contenido.comprobante;
                        order.hks_fiscal_number = clean.ORDER_NUMBER;
                    }
                    order.hks_legacy_state = 'processed';
                    order.save_to_db();
                    resolve(response);
                } catch (error) { reject(error); }
            });
        });
    }

    function fiscalIdentity(pos) {
        const identity = pos && pos.config && pos.config.hks_ff_identity;
        const keys = ['odoo_database', 'odoo_company_id', 'odoo_pos_config_id', 'emisor', 'ambiente', 'sucursal', 'punto'];
        if (!identity || Object.keys(identity).length !== keys.length ||
            keys.some((key) => typeof identity[key] !== 'string' || !identity[key] || identity[key] !== identity[key].trim() || /[\r\n|]/.test(identity[key])) ||
            !['1', '2'].includes(identity.ambiente) || !/^[A-Z0-9]{4}$/.test(identity.sucursal) ||
            !/^[0-9]{3}$/.test(identity.punto) || identity.punto === '000' ||
            !/^[1-9][0-9]*$/.test(identity.odoo_company_id) || !/^[1-9][0-9]*$/.test(identity.odoo_pos_config_id)) {
            throw new Error('Falta el perfil fiscal del TPV. Configure el módulo 4.1.5 y recargue el POS después de conservar los pedidos pendientes.');
        }
        const companyId = Array.isArray(pos.config.company_id) ? pos.config.company_id[0] : pos.config.company_id;
        if (String(pos.config.id) !== identity.odoo_pos_config_id || String(companyId) !== identity.odoo_company_id ||
            !pos.company || String(pos.company.id) !== identity.odoo_company_id) {
            throw new Error('La identidad fiscal no coincide con la compañía y el TPV de esta sesión. No se enviará la venta.');
        }
        return {...identity};
    }

    function newOrderUid() {
        const bytes = new Uint8Array(16);
        window.crypto.getRandomValues(bytes);
        return Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('');
    }

    function canonical(value) {
        if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
        if (value !== null && typeof value === 'object') {
            return '{' + Object.keys(value).sort().map((key) => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}';
        }
        return JSON.stringify(value);
    }

    function fiscalRequestStatus(socket, order, payload, timeoutMs = 5000) {
        return new Promise((resolve, reject) => {
            let settled = false;
            const timer = setTimeout(() => {
                settled = true;
                reject(new Error('No se pudo confirmar si el intento fiscal anterior alcanzó a FF. Conserve el pedido y no cambie sus datos.'));
            }, Math.max(1, Math.min(timeoutMs, 5000)));
            socket.emit('fiscal_request_status', {
                FISCAL_PROTOCOL: PROTOCOL_VERSION,
                FISCAL_IDENTITY: payload.FISCAL_IDENTITY,
                ORDER_UID: order.hks_fiscal_uid,
            }, (response) => {
                if (settled) return;
                settled = true;
                clearTimeout(timer);
                try {
                    if (!response || response.protocol !== PROTOCOL_VERSION ||
                        response.order_uid !== order.hks_fiscal_uid) {
                        throw new Error('El conector no confirmó el estado del pedido fiscal. Use 5.2.8 o posterior y no reintente con datos distintos.');
                    }
                    if (response.error) throw new Error(response.error);
                    if (canonical(response.fiscal_identity) !== canonical(payload.FISCAL_IDENTITY)) {
                        throw new Error('La consulta fiscal pertenece a otro emisor o TPV.');
                    }
                    resolve(response);
                } catch (error) {
                    reject(error);
                }
            });
        });
    }

    async function requestFiscal(socket, order, payload, timeoutMs = FISCAL_ACK_TIMEOUT_MS) {
        if (order.hks_legacy_pending || (order.cufe && !order.hks_fiscal_number)) {
            throw new Error('Pedido anterior al cambio de numeración. Concilie su estado con FF antes de recuperarlo; no se renumerará automáticamente.');
        }
        assertOrderMode(order, 'local');
        const cleanPayload = JSON.parse(JSON.stringify(payload));
        if (order.hks_fiscal_request && canonical(order.hks_fiscal_request) !== canonical(cleanPayload)) {
            if (canonical(order.hks_fiscal_request.FISCAL_IDENTITY) !== canonical(cleanPayload.FISCAL_IDENTITY)) {
                throw new Error('La identidad fiscal cambió después del primer intento. No se consultará ni reenviará este pedido.');
            }
            const status = await fiscalRequestStatus(socket, order, cleanPayload, timeoutMs);
            if (!status.request_mutable || !['missing', 'prepared'].includes(status.state)) {
                throw new Error('La venta cambió después de solicitar un número fiscal y el conector no autoriza modificarla. Concilie con FF; no cree otra venta para reintentar.');
            }
            if (status.state === 'prepared') {
                if (!/^[0-9]{10}$/.test(status.fiscal_number || '') || status.fiscal_number === '0000000000' ||
                    (order.hks_fiscal_number && order.hks_fiscal_number !== status.fiscal_number)) {
                    throw new Error('El número preparado no coincide con este pedido. Detenga y concilie con FF.');
                }
                order.hks_fiscal_number = status.fiscal_number;
            } else if (status.fiscal_number || order.hks_fiscal_number) {
                throw new Error('El pedido figura sin registro local pero Odoo conserva un número. Detenga y concilie antes de continuar.');
            }
            // missing: nada se reservó. prepared: el conector confirmó que el
            // TXT aún no fue publicado y validará que solo cambien los pagos.
            order.hks_fiscal_request = cleanPayload;
            order.hks_fiscal_state = status.state;
        }
        order.hks_fiscal_request = order.hks_fiscal_request || cleanPayload;
        // Debe persistir ANTES del socket: también ante timeout o recarga.
        order.save_to_db();
        if (order.hks_fiscal_state === 'processed') return { procesado: true, replayed: true };

        return new Promise((resolve, reject) => {
            let settled = false;
            const timer = setTimeout(() => {
                settled = true;
                reject(new Error('El conector no confirmó la respuesta. Conserve este pedido y vuelva a consultar. No borre la caché ni genere otra venta.'));
            }, timeoutMs);
            socket.emit('new_order_local', {
                ...order.hks_fiscal_request,
                FISCAL_NUMBER: order.hks_fiscal_number || false,
            }, (response) => {
                if (settled) return;
                settled = true;
                clearTimeout(timer);
                try {
                    if (!response || response.protocol !== PROTOCOL_VERSION || response.order_uid !== order.hks_fiscal_uid) {
                        throw new Error('Respuesta fiscal incompatible o de otra venta. Use conector 5.2.8 y módulo 4.1.5 juntos; no cambie el número manualmente.');
                    }
                    if ((response.procesado || response.fiscal_number) &&
                        canonical(response.fiscal_identity) !== canonical(cleanPayload.FISCAL_IDENTITY)) {
                        throw new Error('La respuesta pertenece a otro emisor o TPV. No se asociará su número a esta venta.');
                    }
                    if (response.fiscal_number) {
                        if (!/^[0-9]{10}$/.test(response.fiscal_number) || response.fiscal_number === '0000000000' ||
                            (order.hks_fiscal_number && order.hks_fiscal_number !== response.fiscal_number)) {
                            throw new Error('El número de la respuesta fiscal no coincide con la venta. Concilie con FF.');
                        }
                        order.hks_fiscal_number = response.fiscal_number;
                    }
                    order.hks_fiscal_state = response.procesado ? 'processed' : (response.pending ? 'pending' : 'error');
                    if (response.procesado && !response.sin_documento) {
                        if (!response.contenido || !response.contenido.identificador || !order.hks_fiscal_number) {
                            order.hks_fiscal_state = 'pending';
                            throw new Error('Respuesta fiscal incompleta; conserve el pedido y consulte de nuevo.');
                        }
                        order.cufe = response.contenido.identificador;
                        order.fel_qr = response.contenido.comprobante;
                    }
                    order.save_to_db();
                    if (!response.procesado) throw new Error(response.mensaje || 'El Agente FF no confirmó el documento.');
                    resolve(response);
                } catch (error) {
                    reject(error);
                }
            });
        });
    }

    return { PROTOCOL_VERSION, FISCAL_ACK_TIMEOUT_MS, fiscalMode, assertOrderMode, legacyNumber, requestLegacy, fiscalIdentity, newOrderUid, canonical, fiscalRequestStatus, requestFiscal };
});

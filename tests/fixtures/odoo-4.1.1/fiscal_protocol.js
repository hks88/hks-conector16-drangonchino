odoo.define('hks_pos_offline_FF_gobierno.fiscal_protocol', function () {
    'use strict';
    const PROTOCOL_VERSION = 2;

    function fiscalIdentity(pos) {
        const identity = pos && pos.config && pos.config.hks_ff_identity;
        const keys = ['odoo_database', 'odoo_company_id', 'odoo_pos_config_id', 'emisor', 'ambiente', 'sucursal', 'punto'];
        if (!identity || Object.keys(identity).length !== keys.length ||
            keys.some((key) => typeof identity[key] !== 'string' || !identity[key] || identity[key] !== identity[key].trim() || /[\r\n|]/.test(identity[key])) ||
            !['1', '2'].includes(identity.ambiente) || !/^[0-9]{4}$/.test(identity.sucursal) ||
            !/^[0-9]{3}$/.test(identity.punto) || identity.punto === '000' ||
            !/^[1-9][0-9]*$/.test(identity.odoo_company_id) || !/^[1-9][0-9]*$/.test(identity.odoo_pos_config_id)) {
            throw new Error('Falta el perfil fiscal del TPV. Configure el módulo 4.1.1 y recargue el POS después de conservar los pedidos pendientes.');
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

    async function requestFiscal(socket, order, payload, timeoutMs = 30000) {
        if (order.hks_legacy_pending || (order.cufe && !order.hks_fiscal_number)) {
            throw new Error('Pedido anterior al cambio de numeración. Concilie su estado con FF antes de recuperarlo; no se renumerará automáticamente.');
        }
        const cleanPayload = JSON.parse(JSON.stringify(payload));
        if (order.hks_fiscal_request && canonical(order.hks_fiscal_request) !== canonical(cleanPayload)) {
            throw new Error('La venta cambió después de solicitar un número fiscal. Restaure sus datos originales y concilie con FF; no cree otra venta para reintentar.');
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
                        throw new Error('Respuesta fiscal incompatible o de otra venta. Use conector 5.2.1 y módulo 4.1.1 juntos; no cambie el número manualmente.');
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

    return { PROTOCOL_VERSION, fiscalIdentity, newOrderUid, canonical, requestFiscal };
});

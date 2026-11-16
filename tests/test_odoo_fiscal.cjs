const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
const modulePath = process.env.HKS_ODOO_JS || path.resolve(__dirname, 'fixtures/odoo-4.1.5/static/src/js');
const IDENTITY = {emisor:'TEST-RUC', ambiente:'2', sucursal:'0000', punto:'001',
    odoo_database:'odoo_pruebas', odoo_company_id:'18', odoo_pos_config_id:'24'};

function posData() {return {config:{id:24, company_id:[18, 'COMPAÑÍA PRUEBA'], hks_ff_identity:{...IDENTITY}}, company:{id:18}};}

function load(mode = 'local', socketStub, sourcePath = modulePath) {
    const env = { _t: (value) => value };
    const ui = {blocked:false};
    let protocol, OrderClass, ScreenClass;
    const modules = {};
    class Order {
        constructor(json) { this.pos = posData(); this.pos.config.hks_ff_mode = mode; if (json) this.init_from_JSON(json); }
        init_from_JSON(json) { this.sequence_number = json.sequence_number; }
        export_as_JSON() { return {sequence_number: this.sequence_number}; }
        export_for_printing() { return {}; }
        save_to_db() { this.saved = JSON.parse(JSON.stringify(this.export_as_JSON())); }
    }
    class PaymentScreen {
        constructor() { this.env = env; this.finalizations = 0; this.popups = []; }
        async _finalizeValidation() { this.finalizations++; }
        async showPopup(type, props) { this.popups.push(props); }
    }
    const context = vm.createContext({
        window: {crypto: webcrypto}, Uint8Array, setTimeout, clearTimeout,
        console: {log(){}}, document: {addEventListener(){}, getElementById(){return null;}},
        framework: {blockUI(){ui.blocked=true;}, unblockUI(){ui.blocked=false;}},
        io: () => socketStub || ({on(){}}),
        odoo: {define(name, factory){ modules[name] = factory(name => modules[name]); protocol = modules['hks_pos_offline_FF_gobierno.fiscal_protocol']; }},
        require(name) {
            return ({
                'point_of_sale.PaymentScreen': PaymentScreen,
                'point_of_sale.Registries': {
                    Model: {extend(base, factory){OrderClass = factory(base);}},
                    Component: {extend(base, factory){ScreenClass = factory(base);}},
                },
                'hks_pos_offline_FF_gobierno.fiscal_protocol': protocol,
                'hks_pos_offline_FF_gobierno.fiscal_recovery': modules['hks_pos_offline_FF_gobierno.fiscal_recovery'],
                'point_of_sale.models': {Order},
                'point_of_sale.Gui': {Gui:{}}, 'web.core': {_t: env._t},
                'point_of_sale.utils': {isConnectionError: () => false},
            })[name];
        },
    });
    const protocolPath = path.join(sourcePath, 'fiscal_protocol.js');
    vm.runInContext(fs.readFileSync(fs.existsSync(protocolPath) ? protocolPath : path.join(modulePath, 'fiscal_protocol.js'), 'utf8'), context);
    const recoveryPath = path.join(sourcePath, 'fiscal_recovery.js');
    if (fs.existsSync(recoveryPath)) vm.runInContext(fs.readFileSync(recoveryPath, 'utf8'), context);
    const source = fs.readFileSync(path.join(sourcePath, 'fisc_connect.js'), 'utf8').replace("import framework from 'web.framework';", '');
    vm.runInContext(source, context);
    return {protocol, OrderClass, ScreenClass, ui, recovery:modules['hks_pos_offline_FF_gobierno.fiscal_recovery']};
}

function data(order) {return {FISCAL_PROTOCOL:2, FISCAL_IDENTITY:{...IDENTITY}, ORDER_UID:order.hks_fiscal_uid, IS_REFUND:false, ORDERLINES:[{PRICE:1}]};}
function success(order) {return {protocol:2, fiscal_identity:{...IDENTITY}, order_uid:order.hks_fiscal_uid, fiscal_number:'9981010243', procesado:true,
    contenido:{identificador:'CUFE-TEST',comprobante:'https://example.test/cafe'}};}

test('Odoo espera hasta 60 segundos el ACK fiscal de FF', () => {
    const {protocol} = load();
    assert.equal(protocol.FISCAL_ACK_TIMEOUT_MS, 60000);
});

test('un ACK tardío dentro del límite todavía finaliza la misma venta', async () => {
    const {protocol, OrderClass} = load();
    const order = new OrderClass();
    const response = await protocol.requestFiscal({emit(event, request, ack) {
        assert.equal(event, 'new_order_local');
        setTimeout(() => ack(success(order)), 36);
    }}, order, data(order), 60);
    assert.equal(response.fiscal_number, '9981010243');
    assert.equal(order.hks_fiscal_state, 'processed');
});

test('UID fiscal sobrevive exportación y recarga; no depende de sesión', () => {
    const {OrderClass} = load();
    const order = new OrderClass();
    assert.match(order.hks_fiscal_uid, /^[a-f0-9]{32}$/);
    const restored = new OrderClass(order.export_as_JSON());
    assert.equal(restored.hks_fiscal_uid, order.hks_fiscal_uid);
    assert.equal(restored.hks_legacy_pending, false);
    assert.notEqual(new OrderClass().hks_fiscal_uid, order.hks_fiscal_uid);
});

test('pedidos antiguos quedan bloqueados sin emitir', async () => {
    const {protocol, OrderClass} = load();
    const order = new OrderClass({sequence_number:43});
    assert.equal(order.hks_legacy_pending, true);
    await assert.rejects(protocol.requestFiscal({emit(){assert.fail('No emitir');}}, order, data(order)), /anterior al cambio/);
    assert.equal(order.export_as_JSON().hks_fiscal_protocol, 0);
});

test('solicitud se guarda antes de emitir y resultado se conserva en JSON', async () => {
    const {protocol, OrderClass} = load();
    const order = new OrderClass();
    await protocol.requestFiscal({emit(event, request, ack){
        assert.equal(event, 'new_order_local');
        assert.equal(order.saved.hks_fiscal_request.ORDER_UID, order.hks_fiscal_uid);
        ack(success(order));
    }}, order, data(order));
    const restored = new OrderClass(order.export_as_JSON());
    assert.equal(restored.hks_fiscal_number, '9981010243');
    assert.equal(restored.cufe, 'CUFE-TEST');
    assert.equal(restored.hks_fiscal_state, 'processed');
});

test('reintento de venta respondida no emite otra solicitud', async () => {
    const {protocol, OrderClass} = load();
    const order = new OrderClass();
    await protocol.requestFiscal({emit(event, request, ack){ack(success(order));}}, order, data(order));
    const response = await protocol.requestFiscal({emit(){assert.fail('No reenviar');}}, order, data(order));
    assert.equal(response.replayed, true);
});

test('timeout conserva snapshot y UID para consultar después de recargar', async () => {
    const {protocol, OrderClass} = load();
    const order = new OrderClass();
    await assert.rejects(protocol.requestFiscal({emit(){}}, order, data(order), 1), /no confirmó/);
    const restored = new OrderClass(order.saved);
    await protocol.requestFiscal({emit(event, request, ack){
        assert.equal(request.ORDER_UID, order.hks_fiscal_uid);
        ack(success(restored));
    }}, restored, data(restored));
});

test('respuesta pendiente conserva el número sin marcar procesado', async () => {
    const {protocol, OrderClass} = load();
    const order = new OrderClass();
    await assert.rejects(protocol.requestFiscal({emit(event, request, ack){
        ack({...success(order), procesado:false, pending:true, mensaje:'Pendiente de FF'});
    }}, order, data(order)), /Pendiente de FF/);
    assert.equal(order.saved.hks_fiscal_number, '9981010243');
    assert.equal(order.saved.hks_fiscal_state, 'pending');
});

test('contenido modificado después de solicitar número queda bloqueado', async () => {
    const {protocol, OrderClass} = load();
    const order = new OrderClass();
    await assert.rejects(protocol.requestFiscal({emit(){}}, order, data(order), 1));
    await assert.rejects(protocol.requestFiscal({emit(event, request, ack){
        assert.equal(event, 'fiscal_request_status');
        ack({protocol:2, order_uid:order.hks_fiscal_uid, fiscal_identity:{...IDENTITY},
            state:'dispatched', fiscal_number:'9981010243', request_mutable:false});
    }}, order, {...data(order), ORDERLINES:[{PRICE:2}]}), /no autoriza modificarla/);
});

test('error anterior sin reserva permite corregir pago y reintentar el mismo UID', async () => {
    const {protocol, OrderClass} = load();
    const order = new OrderClass();
    const original = {...data(order), PAYMENTLINES:[{PAY_NAME:'Efectivo', AMOUNT:21}]};
    order.hks_fiscal_request = JSON.parse(JSON.stringify(original));
    order.hks_fiscal_state = 'error';
    order.save_to_db();
    const corrected = {...data(order), PAYMENTLINES:[{PAY_NAME:'Efectivo', AMOUNT:10.40}]};
    const events = [];
    await protocol.requestFiscal({emit(event, request, ack){
        events.push(event);
        if (event === 'fiscal_request_status') {
            ack({protocol:2, order_uid:order.hks_fiscal_uid, fiscal_identity:{...IDENTITY},
                state:'missing', fiscal_number:null, request_mutable:true});
        } else {
            assert.equal(request.ORDER_UID, order.hks_fiscal_uid);
            assert.deepEqual(JSON.parse(JSON.stringify(request.PAYMENTLINES)), corrected.PAYMENTLINES);
            ack(success(order));
        }
    }}, order, corrected);
    assert.deepEqual(events, ['fiscal_request_status', 'new_order_local']);
    assert.equal(order.hks_fiscal_state, 'processed');
    assert.equal(order.hks_fiscal_number, '9981010243');
});

test('solicitud preparada corrige solo pagos conservando el número reservado', async () => {
    const {protocol, OrderClass} = load();
    const order = new OrderClass();
    const original = {...data(order), PAYMENTLINES:[{PAY_NAME:'Efectivo', AMOUNT:2}]};
    order.hks_fiscal_request = JSON.parse(JSON.stringify(original));
    const corrected = {...data(order), PAYMENTLINES:[{PAY_NAME:'Tarjeta', AMOUNT:2}]};
    await protocol.requestFiscal({emit(event, request, ack){
        if (event === 'fiscal_request_status') {
            ack({protocol:2, order_uid:order.hks_fiscal_uid, fiscal_identity:{...IDENTITY},
                state:'prepared', fiscal_number:'9981010243', request_mutable:true});
        } else {
            assert.equal(request.FISCAL_NUMBER, '9981010243');
            assert.deepEqual(JSON.parse(JSON.stringify(request.PAYMENTLINES)), corrected.PAYMENTLINES);
            ack(success(order));
        }
    }}, order, corrected);
    assert.equal(order.hks_fiscal_number, '9981010243');
    assert.equal(order.hks_fiscal_state, 'processed');
});

test('respuesta de otra venta no se asocia al pedido actual', async () => {
    const {protocol, OrderClass} = load();
    const order = new OrderClass();
    await assert.rejects(protocol.requestFiscal({emit(event, request, ack){
        ack({...success(order), order_uid:'0'.repeat(32)});
    }}, order, data(order)), /otra venta/);
    assert.equal(order.cufe, undefined);
});

test('fallo de persistencia local impide enviar el documento', async () => {
    const {protocol, OrderClass} = load();
    const order = new OrderClass();
    order.save_to_db = () => {throw new Error('Almacenamiento lleno');};
    await assert.rejects(protocol.requestFiscal({emit(){assert.fail('No emitir');}}, order, data(order)), /lleno/);
});

test('finalización espera respuesta fiscal y bloquea doble clic', async () => {
    const {ScreenClass} = load();
    const screen = new ScreenClass();
    let resolve, calls=0;
    screen.send_order_fel = () => {calls++; return new Promise(done => {resolve=done;});};
    const pending = screen._finalizeValidation();
    await screen._finalizeValidation();
    assert.equal(calls, 1);
    assert.equal(screen.finalizations, 0);
    resolve();
    await pending;
    assert.equal(screen.finalizations, 1);
});

test('error fiscal no finaliza ni elimina el pedido', async () => {
    const {ScreenClass} = load();
    const screen = new ScreenClass();
    screen.send_order_fel = async () => {throw new Error('FF pendiente');};
    await screen._finalizeValidation();
    assert.equal(screen.finalizations, 0);
    assert.equal(screen.popups[0].body, 'FF pendiente');
    assert.equal(screen.hksSubmitting, false);
});

// Caracterización de interrupciones y respuestas tardías. Estas pruebas documentan
// el comportamiento actual; no significan que ya exista recuperación automática.
// Odoo/Socket.IO se simulan: no se opera una caja ni se emite un documento real.
test('offline: reconectar no consulta el pendiente; validar sin cambios recupera el mismo UID', async () => {
    const handlers = {}, requests = [];
    let order, lateResult = false;
    const socket = {
        on(event, fn) { handlers[event] = fn; },
        emit(event, request, ack) {
            requests.push({event, request});
            assert.equal(event, 'new_order_local');
            ack(lateResult ? success(order) : {...success(order), procesado:false, pending:true, mensaje:'Pendiente de FF'});
        },
    };
    const {OrderClass, ScreenClass, ui} = load('local', socket);
    order = new OrderClass();
    const screen = new ScreenClass(); screen.currentOrder = order;
    screen._build_fiscal_payload = () => data(order);
    handlers.connect();
    await screen._finalizeValidation();
    assert.equal(screen.finalizations, 0);
    assert.equal(order.hks_fiscal_state, 'pending');
    assert.notEqual(order.finalized, true);
    assert.equal(ui.blocked, false);
    const saved = JSON.stringify(order.saved);
    handlers.disconnect();
    lateResult = true;
    handlers.connect();
    assert.equal(requests.length, 1, 'Reconectar no hizo consulta ni recuperacion');
    assert.equal(JSON.stringify(order.saved), saved, 'Reconectar no cambia el payload');
    await screen._finalizeValidation();
    assert.equal(screen.finalizations, 1);
    assert.equal(order.hks_fiscal_state, 'processed');
    assert.equal(requests.length, 2);
    assert.equal(requests[0].request.ORDER_UID, requests[1].request.ORDER_UID);
    assert.equal(requests[1].request.FISCAL_NUMBER, '9981010243');
});

test('offline: cambiar de pollo 1.75 a combo 6.50 reproduce el aviso sin reenviar TXT', async () => {
    const handlers = {}, events = [];
    let order;
    const socket = {
        on(event, fn) { handlers[event] = fn; },
        emit(event, request, ack) {
            events.push(event);
            if (event === 'fiscal_request_status') {
                ack({protocol:2, order_uid:order.hks_fiscal_uid, fiscal_identity:{...IDENTITY},
                    state:'dispatched', fiscal_number:'9981010243', request_mutable:false});
            } else {
                ack({...success(order), procesado:false, pending:true, mensaje:'Pendiente de FF'});
            }
        },
    };
    const {OrderClass, ScreenClass} = load('local', socket);
    order = new OrderClass();
    const original = {...data(order), ORDERLINES:[{ITEM_ID:4543,PRODUCT_NAME:'POLLO FRITO UND',PRICE:1.75}],
        PAYMENTLINES:[{PAY_NAME:'Efectivo',AMOUNT:1.75}]};
    let current = original;
    const screen = new ScreenClass(); screen.currentOrder = order;
    screen._build_fiscal_payload = () => current;
    handlers.connect();
    await screen._finalizeValidation();
    // Simula un cambio comprobado en el respaldo. NO atribuye su autor a la reconexion.
    current = {...original, ORDERLINES:[{ITEM_ID:4389,PRODUCT_NAME:'COMBO 6.50',PRICE:6.50}],
        PAYMENTLINES:[{PAY_NAME:'Efectivo',AMOUNT:6.50}]};
    handlers.disconnect(); handlers.connect();
    await screen._finalizeValidation();
    assert.match(screen.popups.at(-1).body, /La venta cambió.*no autoriza modificarla/);
    assert.deepEqual(events, ['new_order_local','fiscal_request_status']);
    assert.equal(screen.finalizations, 0);
    assert.equal(order.hks_fiscal_request.ORDERLINES[0].ITEM_ID, 4543);
    assert.equal(order.hks_fiscal_number, '9981010243');
});

test('offline: un cambio de nombre con mismo producto e importe tambien exige conciliacion', async () => {
    const {protocol, OrderClass} = load();
    const order = new OrderClass();
    const original = {...data(order), ORDERLINES:[{ITEM_ID:4507,PRODUCT_NAME:'PAPAS FRITAS',PRICE:1.75}]};
    await assert.rejects(protocol.requestFiscal({emit(event, request, ack) {
        ack({...success(order),procesado:false,pending:true,mensaje:'Pendiente de FF'});
    }}, order, original), /Pendiente/);
    const changed = {...original, ORDERLINES:[{...original.ORDERLINES[0],PRODUCT_NAME:'PAPAS FRITAS COMBO'}]};
    const events = [];
    await assert.rejects(protocol.requestFiscal({emit(event, request, ack) {
        events.push(event);
        assert.equal(event, 'fiscal_request_status');
        ack({protocol:2, order_uid:order.hks_fiscal_uid, fiscal_identity:{...IDENTITY},
            state:'dispatched', fiscal_number:'9981010243', request_mutable:false});
    }}, order, changed), /no autoriza modificarla/);
    assert.deepEqual(events, ['fiscal_request_status']);
});

test('identidad viene del TPV cargado y no del selector o del receptor', () => {
    const {protocol} = load();
    const pos = posData();
    pos.user = {company_id:[999, 'OTRA COMPAÑÍA SELECCIONADA']};
    assert.deepEqual({...protocol.fiscalIdentity(pos)}, IDENTITY);
    const identity = protocol.fiscalIdentity(pos);
    identity.emisor = 'NO MUTAR';
    assert.equal(pos.config.hks_ff_identity.emisor, IDENTITY.emisor);
});

test('sucursal FF alfanumérica mayúscula se acepta sin sustituirla', () => {
    const {protocol} = load();
    const pos = posData();
    pos.config.hks_ff_identity.sucursal = '4B00';
    assert.equal(protocol.fiscalIdentity(pos).sucursal, '4B00');
});

test('sucursal FF minúscula, Unicode o con separadores se rechaza', () => {
    const {protocol} = load();
    for (const sucursal of ['4b00', '4-00', '4 B0', '４B00']) {
        const pos = posData();
        pos.config.hks_ff_identity.sucursal = sucursal;
        assert.throws(() => protocol.fiscalIdentity(pos), /Falta el perfil fiscal/);
    }
});

test('assets antiguos o identidad que no corresponde al TPV bloquean la venta', () => {
    const {protocol} = load();
    for (const field of Object.keys(IDENTITY)) {
        const pos = posData();
        delete pos.config.hks_ff_identity[field];
        assert.throws(() => protocol.fiscalIdentity(pos), /Falta el perfil fiscal/);
    }
    for (const change of [(pos) => {pos.config.id=25;}, (pos) => {pos.company.id=19;},
                          (pos) => {pos.config.company_id=[19, 'OTRA'];}]) {
        const pos = posData(); change(pos);
        assert.throws(() => protocol.fiscalIdentity(pos), /no coincide/);
    }
});

test('payload real incluye emisor separado del RUC del cliente y omite ID de sesión como número', () => {
    const {ScreenClass, OrderClass} = load();
    const screen = new ScreenClass();
    const order = new OrderClass();
    order.pos = posData();
    order.pos.pos_session = {id:10001, login_number:104};
    order.sequence_number = 10043;
    order.orderlines = [{product:{id:4, display_name:'PRUEBA'}, refunded_orderline_id:null,
        get_unit_price:()=>1, get_discount:()=>0, get_quantity:()=>1,
        get_price_without_tax:()=>1, get_tax:()=>0, get_price_with_tax:()=>1, get_tax_percentages:()=>[0]}];
    order.paymentlines = [{name:'Efectivo', amount:1}];
    order.partner = {ruc:'RUC-RECEPTOR', tipo_cliente:'02', name:'Cliente',
        country_id:[], state_id:[], distrito_id:[], corregimiento_id:[]};
    order.get_change = () => 0;
    screen.currentOrder = order;
    const payload = screen._build_fiscal_payload();
    assert.deepEqual({...payload.FISCAL_IDENTITY}, IDENTITY);
    assert.equal(payload.RUC, 'RUC-RECEPTOR');
    assert.equal(payload.FISCAL_PROTOCOL, 2);
    assert.equal(payload.ORDER_NUMBER, undefined);
    assert.equal(payload.ORDER_UID, order.hks_fiscal_uid);
});

test('respuesta con mismo UID pero de otro emisor no asigna número ni CUFE', async () => {
    const {protocol, OrderClass} = load();
    const order = new OrderClass();
    await assert.rejects(protocol.requestFiscal({emit(event, request, ack){
        ack({...success(order), fiscal_identity:{...IDENTITY, emisor:'OTRO-RUC'}});
    }}, order, data(order)), /otro emisor/);
    assert.equal(order.hks_fiscal_number, undefined);
    assert.equal(order.cufe, undefined);
});

test('rechazo por compañía incorrecta conserva venta sin número y muestra el error del conector', async () => {
    const {protocol, OrderClass} = load();
    const order = new OrderClass();
    await assert.rejects(protocol.requestFiscal({emit(event, request, ack){
        ack({protocol:2, order_uid:order.hks_fiscal_uid, procesado:false, mensaje:'Otra compañía: use su PC'});
    }}, order, data(order)), /Otra compañía/);
    assert.equal(order.saved.hks_fiscal_number, null);
    assert.equal(order.saved.hks_fiscal_state, 'error');
});

test('no se cambia de emisor para reintentar una venta pendiente', async () => {
    const {protocol, OrderClass} = load();
    const order = new OrderClass();
    await assert.rejects(protocol.requestFiscal({emit(){}}, order, data(order), 1));
    await assert.rejects(protocol.requestFiscal({emit(){assert.fail('No emitir');}}, order,
        {...data(order), FISCAL_IDENTITY:{...IDENTITY, emisor:'OTRO'}}), /identidad fiscal cambió/);
});

test('pedidos del protocolo 1 también requieren conciliación y conector anterior no se acepta', async () => {
    const {protocol, OrderClass} = load();
    const previous = new OrderClass({hks_fiscal_protocol:1, hks_fiscal_uid:'a'.repeat(32)});
    await assert.rejects(protocol.requestFiscal({emit(){assert.fail('No emitir');}}, previous, data(previous)), /anterior al cambio/);
    const order = new OrderClass();
    await assert.rejects(protocol.requestFiscal({emit(event, request, ack){ack({...success(order), protocol:1});}}, order, data(order)), /Respuesta fiscal incompatible/);
});

function legacyData(number = '9981010242') {
    return {ORDER_NUMBER:number, ORDERLINES:[{PRODUCT_NAME:'PRUEBA', PRICE:1}], PAYMENTLINES:[{PAY_NAME:'Efectivo', AMOUNT:1}]};
}
function legacySuccess() {return {procesado:true, contenido:{identificador:'CUFE-SIMULADO', comprobante:'https://example.test/cafe'}};}

test('modo heredado emite los cinco argumentos de 5.1.9 y usa su ACK', async () => {
    const {protocol, OrderClass} = load('odoo');
    const order = new OrderClass();
    await protocol.requestLegacy({emit(event, body, valid, refund, pos, devol, ack) {
        assert.equal(event, 'new_order');
        assert.equal(body.ORDER_NUMBER, '9981010242');
        assert.deepEqual([valid, refund, pos, devol], [true, false, true, true]);
        assert.equal(body.FISCAL_PROTOCOL, undefined);
        assert.equal(order.saved.hks_legacy_state, 'sent');
        ack(legacySuccess());
    }}, order, legacyData(), false);
    assert.equal(order.saved.hks_fiscal_protocol, 0);
    assert.equal(order.saved.hks_fiscal_mode, 'odoo');
    assert.equal(order.cufe, 'CUFE-SIMULADO');
    assert.equal(order.hks_fiscal_number, '9981010242');
});

test('ACK antiguo de Abono sin TXT no consume número fiscal', async () => {
    const {protocol, OrderClass} = load('odoo');
    const order = new OrderClass();
    const payload = legacyData(); payload.ORDERLINES[0].PRODUCT_NAME = 'Abono';
    const result = await protocol.requestLegacy({emit(event, body, valid, refund, pos, devol, ack) {
        assert.equal(devol, false); ack({success:true, file:null});
    }}, order, payload, false);
    assert.equal(result.sin_documento, true);
    assert.equal(order.hks_fiscal_number, undefined);
});

test('timeout antiguo no cambia a local ni permite reenvío tras recarga', async () => {
    const {protocol, OrderClass} = load('odoo');
    const order = new OrderClass(); let emits = 0;
    await assert.rejects(protocol.requestLegacy({emit(event){assert.equal(event,'new_order'); emits++;}}, order, legacyData(), false, 1), /no confirmó/);
    const restored = new OrderClass(order.saved);
    await assert.rejects(protocol.requestLegacy({emit(){emits++;}}, restored, legacyData(), false), /ya fue enviado/);
    assert.equal(emits, 1);
    assert.equal(restored.saved, undefined);
});

test('cambiar de modo no convierte un pedido nuevo ni pendiente', async () => {
    for (const initial of ['odoo','local']) {
        const {protocol, OrderClass} = load(initial);
        const order = new OrderClass();
        order.pos.config.hks_ff_mode = initial === 'odoo' ? 'local' : 'odoo';
        const socket = {emit(){assert.fail('No emitir');}};
        const call = initial === 'odoo' ? protocol.requestFiscal(socket,order,data(order)) : protocol.requestLegacy(socket,order,legacyData(),false);
        await assert.rejects(call, /no coincide con el TPV/);
    }
});

test('un pedido antiguo guardado no se convierte en local ni se reenvía por 5.1.9', async () => {
    const {protocol, OrderClass} = load('odoo');
    const order = new OrderClass({sequence_number:1});
    const socket = {emit(){assert.fail('No emitir');}};
    await assert.rejects(protocol.requestLegacy(socket,order,legacyData(),false), /anterior al cambio/);
    const restored = new OrderClass(order.export_as_JSON());
    assert.equal(restored.hks_legacy_pending, true);
});

test('respuesta antigua ya guardada se reutiliza sin reenviar después de recargar', async () => {
    const {protocol, OrderClass} = load('odoo');
    const order = new OrderClass();
    await protocol.requestLegacy({emit(...args){args.at(-1)(legacySuccess());}}, order, legacyData(), false);
    const restored = new OrderClass(order.saved);
    const response = await protocol.requestLegacy({emit(){assert.fail('No reenviar');}}, restored, legacyData(), false);
    assert.equal(response.replayed, true);
    assert.equal(restored.cufe, 'CUFE-SIMULADO');
    await assert.rejects(protocol.requestLegacy({},restored,legacyData('9981010243'),false), /venta cambió/);
});

test('respuesta antigua incompleta o fallida no finaliza ni habilita otra emisión', async () => {
    for (const response of [null, {success:false,error:'Error de FF'}, {procesado:true}]) {
        const {protocol, OrderClass} = load('odoo');
        const order = new OrderClass();
        await assert.rejects(protocol.requestLegacy({emit(...args){args.at(-1)(response);}},order,legacyData(),false));
        assert.equal(order.hks_legacy_state, 'sent');
        assert.equal(order.cufe, undefined);
    }
});

test('fórmula antigua conserva número válido y bloquea overflow de sesión/login/orden', () => {
    const {protocol, OrderClass} = load('odoo');
    const order = new OrderClass();
    order.pos.pos_session = {id:9981,login_number:1}; order.sequence_number=242;
    assert.equal(protocol.legacyNumber(order),'9981010242');
    for (const [id,login,sequence] of [[10001,4,43],[1,100,1],[1,1,10000]]) {
        order.pos.pos_session = {id,login_number:login}; order.sequence_number=sequence;
        assert.throws(() => protocol.legacyNumber(order),/10 dígitos/);
    }
});

test('fallo al guardar pedido antiguo impide emitir y deja intentar persistir de nuevo', async () => {
    const {protocol, OrderClass} = load('odoo');
    const order = new OrderClass(); order.save_to_db = () => {throw new Error('Disco lleno');};
    await assert.rejects(protocol.requestLegacy({emit(){assert.fail('No enviar');}}, order, legacyData(), false), /Disco lleno/);
    assert.equal(order.hks_legacy_state, null);
});

test('4.1.1 local guardado sin campo de modo sigue siendo protocolo 2', () => {
    const {OrderClass} = load();
    const order = new OrderClass({hks_fiscal_protocol:2,hks_fiscal_uid:'a'.repeat(32)});
    assert.equal(order.hks_fiscal_mode, 'local');
    assert.equal(order.hks_legacy_pending, false);
    assert.equal(order.export_as_JSON().hks_fiscal_protocol, 2);
});

test('payload heredado real coincide con el módulo 4.0 original', async () => {
    const originalPath = path.resolve(__dirname, 'fixtures/odoo-4.0');
    let captured;
    const old = load('odoo',{on(){},emit(...args){captured=args;}},originalPath);
    const modern = load('odoo');
    function fixture(classes) {
        const screen = new classes.ScreenClass(), order = new classes.OrderClass();
        order.pos.pos_session = {id:9981,login_number:1}; order.sequence_number=242;
        order.orderlines = [{id:71,product:{id:4,display_name:'PRUEBA'},refunded_orderline_id:null,
            get_unit_price:()=>1,get_discount:()=>0,get_quantity:()=>1,get_price_without_tax:()=>1,
            get_tax:()=>0,get_price_with_tax:()=>1,get_tax_percentages:()=>[0]}];
        order.paymentlines=[{name:'Efectivo',amount:1}]; order.partner=null; order.get_change=()=>0;
        screen.currentOrder=order; return screen;
    }
    await fixture(old).send_order_fel();
    const expected = JSON.parse(JSON.stringify(captured[1]));
    const actual = JSON.parse(JSON.stringify(fixture(modern)._build_fiscal_payload()));
    assert.deepEqual(actual, expected);
    assert.equal(captured[0],'new_order');
});

test('solicitud pendiente generada por código real 4.1.1 sobrevive actualización a 4.1.2', async () => {
    const oldPath = path.resolve(__dirname, 'fixtures/odoo-4.1.1');
    const old = load('local',undefined,oldPath);
    const order = new old.OrderClass();
    await assert.rejects(old.protocol.requestFiscal({emit(event,request,ack){
        ack({...success(order),procesado:false,pending:true,mensaje:'Pendiente'});
    }},order,data(order)),/Pendiente/);
    const snapshot=order.export_as_JSON();
    assert.equal(snapshot.hks_fiscal_mode,undefined);
    const modern=load();
    const restored=new modern.OrderClass(snapshot);
    await modern.protocol.requestFiscal({emit(event,request,ack){
        assert.equal(event,'new_order_local');
        assert.equal(request.FISCAL_NUMBER,snapshot.hks_fiscal_number);
        assert.equal(request.ORDER_UID,snapshot.hks_fiscal_uid);
        ack(success(restored));
    }},restored,data(restored));
    assert.equal(restored.hks_fiscal_number,snapshot.hks_fiscal_number);
    assert.equal(restored.hks_fiscal_mode,'local');
});

// Reused by the candidate checks; the same suite can target either module.
module.exports = {load, posData, data, success, IDENTITY};

"""Identidad explícita del emisor y del TPV; nunca se toma del receptor."""
import re

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class PosConfig(models.Model):
    _inherit = 'pos.config'

    hks_ff_mode = fields.Selection([('odoo', 'Heredado — conector 5.1.9'),
                                     ('local', 'Contador local — conector 5.2.8')],
                                    string='Numeración FF', required=True, default='odoo', copy=False,
                                    help='Cambiar solo con la sesión cerrada, pedidos y colas conciliados y PC preparada. No vuelve automáticamente al modo heredado.')

    hks_ff_emisor = fields.Char(string='RUC emisor FF', copy=False,
                               help='RUC exacto del emisor configurado en el Agente FF de esta PC. No es el RUC de un cliente.')
    hks_ff_ambiente = fields.Selection([('1', 'Producción'), ('2', 'Pruebas')],
                                      string='Ambiente FF', copy=False)
    hks_ff_sucursal = fields.Char(string='Sucursal FF (4 caracteres A-Z/0-9)', copy=False)
    hks_ff_punto = fields.Char(string='Punto FF (3 dígitos)', copy=False)
    hks_ff_config_info = fields.Text(string='Vinculación de esta PC', compute='_compute_hks_ff_config_info')
    hks_ff_scope_key = fields.Char(compute='_compute_hks_ff_scope_key', store=True, copy=False)

    _sql_constraints = [
        ('hks_ff_unique_scope', 'unique(hks_ff_scope_key)',
         'Este RUC, ambiente, sucursal y punto FF ya están vinculados a otro TPV. No cree dos contadores independientes para el mismo emisor y punto.'),
    ]

    @api.depends('hks_ff_emisor', 'hks_ff_ambiente', 'hks_ff_sucursal', 'hks_ff_punto')
    def _compute_hks_ff_scope_key(self):
        for config in self:
            parts = ((config.hks_ff_emisor or '').strip().upper(), config.hks_ff_ambiente,
                     config.hks_ff_sucursal, config.hks_ff_punto)
            # Unicidad por emisor/punto incluso si los TPV son de compañías Odoo distintas.
            config.hks_ff_scope_key = '|'.join(parts) if all(parts) else False

    @api.depends('hks_ff_mode', 'company_id', 'hks_ff_emisor', 'hks_ff_ambiente', 'hks_ff_sucursal', 'hks_ff_punto')
    def _compute_hks_ff_config_info(self):
        for config in self:
            if config.hks_ff_mode == 'odoo':
                config.hks_ff_config_info = (
                    'Modo heredado: conserva el protocolo y la numeración de Odoo con conector 5.1.9 o 5.2.8 en modo=odoo. '
                    'No corrige secuencias de más de 10 dígitos. No inicialice el contador hasta detener esta caja y conciliar FF. '
                    'Antes de activar local, conserve pedidos pendientes, cierre la sesión y prepare el perfil y la PC.'
                )
                continue
            config.hks_ff_config_info = (
                '[FiscalSequence]\nmodo = local\n'
                'odoo_database = %s\nodoo_company_id = %s\nodoo_pos_config_id = %s\n'
                'emisor = %s\nambiente = %s\n\n'
                '[Target]\nsucursal = %s\npunto_de_facturacion = %s'
            ) % (self.env.cr.dbname, config.company_id.id or '',
                 config.id if isinstance(config.id, int) else '',
                 (config.hks_ff_emisor or '').strip(), config.hks_ff_ambiente or '',
                 config.hks_ff_sucursal or '', config.hks_ff_punto or '')

    def write(self, vals):
        protected = {'hks_ff_mode', 'company_id', 'hks_ff_emisor', 'hks_ff_ambiente', 'hks_ff_sucursal', 'hks_ff_punto'}
        for config in self:
            changed = [name for name in protected.intersection(vals)
                       if (config.company_id.id if name == 'company_id' else config[name]) != vals[name]]
            if not changed:
                continue
            if config.has_active_session:
                raise ValidationError(_('Cierre la sesión de este TPV y concilie sus pedidos antes de cambiar el modo o el perfil fiscal.'))
            if config.hks_ff_mode == 'local':
                raise ValidationError(_('Este TPV ya usa contador local. No se permite volver al modo heredado ni cambiar su identidad fiscal; conserve el estado y contacte soporte.'))
        return super().write(vals)

    @api.constrains('hks_ff_mode', 'company_id', 'hks_ff_emisor', 'hks_ff_ambiente', 'hks_ff_sucursal', 'hks_ff_punto')
    def _check_hks_ff_local_ready(self):
        for config in self:
            if config.hks_ff_mode == 'local':
                config._hks_ff_identity()

    @api.constrains('hks_ff_emisor', 'hks_ff_sucursal', 'hks_ff_punto')
    def _check_hks_ff_profile(self):
        for config in self:
            if config.hks_ff_emisor and (not config.hks_ff_emisor.strip() or
                                        any(c in config.hks_ff_emisor for c in '\r\n|')):
                raise ValidationError(_('RUC emisor FF inválido.'))
            if config.hks_ff_sucursal and not re.fullmatch(r'[A-Z0-9]{4}', config.hks_ff_sucursal):
                raise ValidationError(_('Sucursal FF debe tener exactamente 4 caracteres en mayúscula (A-Z o 0-9).'))
            if config.hks_ff_punto and (not re.fullmatch(r'[0-9]{3}', config.hks_ff_punto) or config.hks_ff_punto == '000'):
                raise ValidationError(_('Punto FF debe estar entre 001 y 999.'))

    def _hks_ff_identity(self):
        self.ensure_one()
        self._check_hks_ff_profile()
        if not (self.hks_ff_emisor and self.hks_ff_ambiente in ('1', '2') and
                self.hks_ff_sucursal and self.hks_ff_punto and self.company_id):
            raise ValidationError(_('Complete el perfil Factura Fácil de este TPV y vincule su PC antes de abrir el POS.'))
        return {
            'odoo_database': self.env.cr.dbname,
            'odoo_company_id': str(self.company_id.id),
            'odoo_pos_config_id': str(self.id),
            'emisor': self.hks_ff_emisor.strip(),
            'ambiente': self.hks_ff_ambiente,
            'sucursal': self.hks_ff_sucursal,
            'punto': self.hks_ff_punto,
        }


class PosSession(models.Model):
    _inherit = 'pos.session'

    def _get_pos_ui_pos_config(self, params):
        config = super()._get_pos_ui_pos_config(params)
        # Se deriva del TPV de la sesión, no de env.company ni del selector multicompañía.
        config['hks_ff_mode'] = self.config_id.hks_ff_mode
        config['hks_ff_identity'] = self.config_id._hks_ff_identity() if self.config_id.hks_ff_mode == 'local' else False
        return config

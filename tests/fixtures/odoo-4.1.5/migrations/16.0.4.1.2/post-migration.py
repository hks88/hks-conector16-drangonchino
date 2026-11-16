"""4.0 queda heredado; 4.1.0/4.1.1 conserva su modo local al actualizar."""


def migrate(cr, version):
    previous = tuple(int(part) for part in version.split('.')) if version else ()
    if previous in ((4, 1, 0), (4, 1, 1), (16, 0, 4, 1, 0), (16, 0, 4, 1, 1)):
        # No activa un contador nuevo ni toca pedidos. Es preservación del modo
        # que ya imponían esas versiones, incluso si el perfil estaba incompleto.
        cr.execute("UPDATE pos_config SET hks_ff_mode = 'local'")

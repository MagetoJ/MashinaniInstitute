# Immutable production image: stock Odoo 18 + the UMTTI module baked in.
FROM odoo:18.0
USER root
COPY --chown=odoo:odoo umtti_school_management /mnt/extra-addons/umtti_school_management
USER odoo

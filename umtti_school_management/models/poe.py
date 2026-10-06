from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


class UmttiPoe(models.Model):
    """e-PoE: a student's electronic Portfolio of Evidence for one unit."""
    _name = 'umtti.poe'
    _description = 'Portfolio of Evidence'
    _inherit = ['mail.thread']
    _order = 'id desc'

    name = fields.Char(compute='_compute_name', store=True)
    student_id = fields.Many2one('res.partner', required=True, domain=[('is_student', '=', True)],
                                 index=True, tracking=True)
    course_id = fields.Many2one(related='student_id.course_id', store=True)
    unit_id = fields.Many2one('umtti.course.unit', required=True, domain="[('course_id', '=', course_id)]")
    assessor_id = fields.Many2one('hr.employee', string='Assessor', domain=[('is_trainer', '=', True)],
                                  tracking=True)
    state = fields.Selection([('draft', 'Draft'), ('submitted', 'Submitted'), ('verified', 'Verified')],
                             default='draft', tracking=True)
    entry_ids = fields.One2many('umtti.poe.entry', 'poe_id', string='Evidence')
    entry_count = fields.Integer(compute='_compute_progress', store=True)
    verified_count = fields.Integer(compute='_compute_progress', store=True)
    progress = fields.Float(compute='_compute_progress', store=True, string='Verified %')

    _sql_constraints = [
        ('poe_uniq', 'unique(student_id, unit_id)', 'This student already has a portfolio for this unit.'),
    ]

    @api.depends('student_id', 'unit_id')
    def _compute_name(self):
        for rec in self:
            rec.name = '%s - %s' % (rec.student_id.name or '', rec.unit_id.name or '')

    @api.depends('entry_ids.state')
    def _compute_progress(self):
        for rec in self:
            total = len(rec.entry_ids)
            ok = len(rec.entry_ids.filtered(lambda e: e.state == 'verified'))
            rec.entry_count = total
            rec.verified_count = ok
            rec.progress = 100.0 * ok / total if total else 0.0

    def action_submit(self):
        for rec in self:
            if not rec.entry_ids:
                raise ValidationError(_('Add at least one piece of evidence before submitting.'))
        self.write({'state': 'submitted'})

    def action_verify(self):
        for rec in self:
            if rec.entry_ids.filtered(lambda e: e.state != 'verified'):
                raise ValidationError(_('Every piece of evidence must be verified first.'))
        self.write({'state': 'verified'})

    def action_reset(self):
        if not self.env.user.has_group('umtti_school_management.group_umtti_user'):
            raise AccessError(_('Only officers or managers can reopen a portfolio.'))
        self.write({'state': 'draft'})


class UmttiPoeEntry(models.Model):
    _name = 'umtti.poe.entry'
    _description = 'PoE Evidence Entry'
    _order = 'poe_id, date, id'

    poe_id = fields.Many2one('umtti.poe', required=True, ondelete='cascade', index=True)
    title = fields.Char(required=True)
    date = fields.Date(default=fields.Date.context_today)
    evidence_type = fields.Selection([
        ('practical', 'Practical Work'),
        ('photo', 'Photo'),
        ('video', 'Video'),
        ('document', 'Document / Report'),
        ('observation', 'Observation Checklist'),
        ('other', 'Other'),
    ], required=True, default='practical')
    description = fields.Text()
    attachment_ids = fields.Many2many('ir.attachment', 'umtti_poe_entry_attachment_rel', 'entry_id',
                                      'attachment_id', string='Files')
    state = fields.Selection([('submitted', 'Submitted'), ('verified', 'Verified'), ('rejected', 'Rejected')],
                             default='submitted')
    assessor_comment = fields.Char()

    def action_verify(self):
        self.write({'state': 'verified'})

    def action_reject(self):
        for rec in self:
            if not rec.assessor_comment:
                raise ValidationError(_('Give a comment explaining why the evidence is rejected.'))
        self.write({'state': 'rejected'})

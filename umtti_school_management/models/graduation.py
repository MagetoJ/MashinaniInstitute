from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


class UmttiGraduation(models.Model):
    _name = 'umtti.graduation'
    _description = 'Graduation'
    _inherit = ['mail.thread']
    _order = 'date desc, id desc'

    name = fields.Char(required=True)
    class_id = fields.Many2one('umtti.class', required=True)
    course_id = fields.Many2one(related='class_id.course_id', store=True)
    date = fields.Date(required=True, default=fields.Date.context_today)
    state = fields.Selection([('draft', 'Draft'), ('done', 'Processed')], default='draft', tracking=True)
    line_ids = fields.One2many('umtti.graduation.line', 'graduation_id', string='Graduands')
    graduated_count = fields.Integer(compute='_compute_counts')
    withheld_count = fields.Integer(compute='_compute_counts')

    @api.depends('line_ids.decision')
    def _compute_counts(self):
        for rec in self:
            rec.graduated_count = len(rec.line_ids.filtered(lambda l: l.decision == 'graduated'))
            rec.withheld_count = len(rec.line_ids.filtered(lambda l: l.decision == 'withheld'))

    def action_generate_lines(self):
        for rec in self.filtered(lambda r: r.state == 'draft'):
            have = rec.line_ids.mapped('student_id')
            self.env['umtti.graduation.line'].create([
                {'graduation_id': rec.id, 'student_id': s.id} for s in rec.class_id.student_ids - have])

    def action_process(self):
        if not self.env.user.has_group('umtti_school_management.group_umtti_manager'):
            raise AccessError(_('Only managers can process a graduation.'))
        Seq = self.env['ir.sequence']
        for rec in self:
            if not rec.line_ids:
                raise ValidationError(_('Generate the graduand list first.'))
            for line in rec.line_ids.filtered(lambda l: l.decision == 'pending'):
                if line.eligible or line.override:
                    line.write({'decision': 'graduated',
                                'certificate_no': Seq.next_by_code('umtti.certificate.no')})
                    line.student_id.action_complete()
                else:
                    line.decision = 'withheld'
            rec.state = 'done'


class UmttiGraduationLine(models.Model):
    _name = 'umtti.graduation.line'
    _description = 'Graduand'
    _order = 'graduation_id, student_id'

    graduation_id = fields.Many2one('umtti.graduation', required=True, ondelete='cascade', index=True)
    student_id = fields.Many2one('res.partner', required=True, domain=[('is_student', '=', True)])
    eligible = fields.Boolean(compute='_compute_eligibility')
    eligibility_note = fields.Char(compute='_compute_eligibility')
    override = fields.Boolean(string='Manager Override',
                              help='Graduate even though the requirements are not all met.')
    decision = fields.Selection([('pending', 'Pending'), ('graduated', 'Graduated'), ('withheld', 'Withheld')],
                                default='pending')
    certificate_no = fields.Char(copy=False, readonly=True)

    _sql_constraints = [
        ('grad_student_uniq', 'unique(graduation_id, student_id)', 'Student already listed.'),
    ]

    def _compute_eligibility(self):
        Poe = self.env['umtti.poe'].sudo()
        for rec in self:
            student = rec.student_id
            units = student.course_id.unit_ids
            data = student.get_transcript_data() if student else {'units': []}
            scored = {u['unit_id']: u for u in data['units']}
            verified = set(Poe.search([('student_id', '=', student.id), ('state', '=', 'verified')]).unit_id.ids)
            notes = []
            if not units:
                notes.append(_('Course has no units'))
            if units.filtered(lambda u: u.id not in scored):
                notes.append(_('Missing assessment results'))
            if any(not u['competent'] for u in data['units']):
                notes.append(_('Not yet competent in some units'))
            if units.filtered(lambda u: u.id not in verified):
                notes.append(_('PoE not verified for all units'))
            rec.eligible = not notes
            rec.eligibility_note = '; '.join(notes) or _('All requirements met')

    def write(self, vals):
        if 'override' in vals and not self.env.user.has_group('umtti_school_management.group_umtti_manager'):
            raise AccessError(_('Only managers can override graduation requirements.'))
        return super().write(vals)


class UmttiToolIssue(models.Model):
    """Tools / equipment issued to a student (training use or graduation start-up kit)."""
    _name = 'umtti.tool.issue'
    _description = 'Tool / Equipment Assignment'
    _inherit = ['mail.thread']
    _order = 'date_issued desc, id desc'

    student_id = fields.Many2one('res.partner', required=True, domain=[('is_student', '=', True)],
                                 index=True, tracking=True)
    tool = fields.Char(string='Tool / Equipment', required=True)
    serial_no = fields.Char(string='Serial / Tag No')
    quantity = fields.Float(default=1.0)
    purpose = fields.Selection([('training', 'Training Use'), ('startup', 'Graduation Start-up Kit')],
                               default='training', required=True)
    date_issued = fields.Date(default=fields.Date.context_today, required=True)
    date_returned = fields.Date()
    state = fields.Selection([('issued', 'Issued'), ('returned', 'Returned'), ('lost', 'Lost / Written off')],
                             default='issued', tracking=True)
    notes = fields.Char()

    @api.constrains('quantity')
    def _check_quantity(self):
        for rec in self:
            if rec.quantity <= 0:
                raise ValidationError(_('Quantity must be greater than zero.'))

    def action_return(self):
        self.write({'state': 'returned', 'date_returned': fields.Date.context_today(self)})

    def action_lost(self):
        self.write({'state': 'lost'})

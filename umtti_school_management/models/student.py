from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

DOCUMENT_TYPES = [
    ('kcse', 'KCSE Certificate'),
    ('kcpe', 'KCPE Certificate'),
    ('leaving_cert', 'Leaving Certificate'),
    ('passport_photos', 'Passport Photos'),
    ('id_copy', 'ID Copy'),
    ('birth_cert', 'Birth Certificate'),
]


class ResPartner(models.Model):
    _inherit = 'res.partner'

    is_student = fields.Boolean(string='Is a Student', index=True)
    admission_no = fields.Char(string='Admission No', copy=False, readonly=True, index=True)
    admission_date = fields.Date(default=fields.Date.context_today)
    date_of_birth = fields.Date()
    age = fields.Integer(compute='_compute_age', string='Age')
    id_no = fields.Char(string='ID No', copy=False)
    gender = fields.Selection([('male', 'Male'), ('female', 'Female'), ('other', 'Other')])
    education_level = fields.Selection([
        ('kcpe', 'KCPE'),
        ('kcse', 'KCSE'),
        ('certificate', 'Certificate'),
        ('diploma', 'Diploma'),
        ('degree', 'Degree'),
        ('other', 'Other'),
    ])
    county = fields.Char()
    conservancy = fields.Char()
    marital_status = fields.Selection([
        ('single', 'Single'), ('married', 'Married'),
        ('divorced', 'Divorced'), ('widowed', 'Widowed'),
    ], string='Student Marital Status')
    course_id = fields.Many2one('umtti.course', string='Course', tracking=True)
    date_completed = fields.Date()
    student_state = fields.Selection([
        ('applicant', 'Applicant'),
        ('enrolled', 'Enrolled'),
        ('completed', 'Completed'),
        ('withdrawn', 'Withdrawn'),
    ], default='applicant', string='Student Status')
    next_of_kin_name = fields.Char(string='Next of Kin Name')
    next_of_kin_phone = fields.Char(string='Next of Kin Phone')

    document_check_ids = fields.One2many('umtti.student.document', 'partner_id', string='Verification Checklist')
    documents_complete = fields.Boolean(compute='_compute_documents_complete', store=True)
    equipment_line_ids = fields.One2many('umtti.student.equipment', 'partner_id', string='Equipment Checklist')
    equipment_complete = fields.Boolean(compute='_compute_equipment_complete', store=True)

    _sql_constraints = [
        ('admission_no_uniq', 'unique(admission_no)', 'Admission number must be unique.'),
    ]

    @api.depends('date_of_birth')
    def _compute_age(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.age = relativedelta(today, rec.date_of_birth).years if rec.date_of_birth else 0

    @api.depends('document_check_ids.received')
    def _compute_documents_complete(self):
        for rec in self:
            rec.documents_complete = bool(rec.document_check_ids) and all(rec.document_check_ids.mapped('received'))

    @api.depends('equipment_line_ids.qty_provided', 'equipment_line_ids.qty_required')
    def _compute_equipment_complete(self):
        for rec in self:
            lines = rec.equipment_line_ids
            rec.equipment_complete = bool(lines) and all(l.qty_provided >= l.qty_required for l in lines)

    @api.constrains('id_no')
    def _check_id_no_unique(self):
        for rec in self.filtered('id_no'):
            if self.search_count([('id_no', '=', rec.id_no), ('id', '!=', rec.id), ('is_student', '=', True)]):
                raise ValidationError(_('ID No %s is already registered to another student.', rec.id_no))

    @api.constrains('date_of_birth')
    def _check_dob(self):
        for rec in self.filtered('date_of_birth'):
            if rec.date_of_birth > fields.Date.context_today(rec):
                raise ValidationError(_('Date of birth cannot be in the future.'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('is_student') and not vals.get('admission_no'):
                vals['admission_no'] = self.env['ir.sequence'].next_by_code('umtti.admission.no')
        records = super().create(vals_list)
        for rec in records.filtered('is_student'):
            rec._init_document_checklist()
            rec._generate_equipment_checklist()
        return records

    def write(self, vals):
        if vals.get('is_student'):
            for rec in self.filtered(lambda r: not r.admission_no):
                rec.admission_no = self.env['ir.sequence'].next_by_code('umtti.admission.no')
        res = super().write(vals)
        if vals.get('is_student'):
            for rec in self.filtered(lambda r: not r.document_check_ids):
                rec._init_document_checklist()
        if 'course_id' in vals:
            self._generate_equipment_checklist()
        return res

    def _init_document_checklist(self):
        self.ensure_one()
        existing = self.document_check_ids.mapped('document_type')
        self.env['umtti.student.document'].create([
            {'partner_id': self.id, 'document_type': code}
            for code, _label in DOCUMENT_TYPES if code not in existing
        ])

    def _generate_equipment_checklist(self):
        """(Re)build equipment lines from the course. Keeps lines already issued/received."""
        Line = self.env['umtti.student.equipment']
        for rec in self.filtered('is_student'):
            rec.equipment_line_ids.filtered(lambda l: not l.qty_provided).unlink()
            kept = rec.equipment_line_ids.mapped('item_id')
            Line.create([
                {'partner_id': rec.id, 'item_id': item.id, 'qty_required': item.quantity}
                for item in rec.course_id.equipment_item_ids - kept
            ])

    def action_regenerate_equipment(self):
        self._generate_equipment_checklist()

    def action_enroll(self):
        for rec in self:
            if not rec.documents_complete:
                raise ValidationError(_('All verification documents must be received before enrolment.'))
            if not rec.course_id:
                raise ValidationError(_('Select a course before enrolment.'))
            rec.student_state = 'enrolled'

    def action_complete(self):
        self.write({'student_state': 'completed', 'date_completed': fields.Date.context_today(self)})

    def action_withdraw(self):
        self.write({'student_state': 'withdrawn'})


class UmttiStudentDocument(models.Model):
    _name = 'umtti.student.document'
    _description = 'Student Verification Document'
    _order = 'id'

    partner_id = fields.Many2one('res.partner', required=True, ondelete='cascade', index=True)
    document_type = fields.Selection(DOCUMENT_TYPES, required=True)
    received = fields.Boolean()
    date_received = fields.Date()
    notes = fields.Char()

    _sql_constraints = [
        ('doc_uniq', 'unique(partner_id, document_type)', 'Document already listed for this student.'),
    ]

    @api.onchange('received')
    def _onchange_received(self):
        self.date_received = fields.Date.context_today(self) if self.received else False


class UmttiStudentEquipment(models.Model):
    _name = 'umtti.student.equipment'
    _description = 'Student Equipment Checklist Line'
    _order = 'id'

    partner_id = fields.Many2one('res.partner', required=True, ondelete='cascade', index=True)
    item_id = fields.Many2one('umtti.equipment.item', string='Equipment', required=True)
    course_id = fields.Many2one(related='item_id.course_id', store=True)
    qty_required = fields.Float(string='Required')
    qty_provided = fields.Float(string='Provided')
    state = fields.Selection([
        ('pending', 'Pending'), ('partial', 'Partial'), ('complete', 'Complete'),
    ], compute='_compute_state', store=True)
    notes = fields.Char()

    @api.depends('qty_required', 'qty_provided')
    def _compute_state(self):
        for rec in self:
            if rec.qty_provided <= 0:
                rec.state = 'pending'
            elif rec.qty_provided < rec.qty_required:
                rec.state = 'partial'
            else:
                rec.state = 'complete'

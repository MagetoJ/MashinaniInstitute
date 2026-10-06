from odoo import api, fields, models


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    is_trainer = fields.Boolean(string='Is a Trainer', index=True)
    tivet_no = fields.Char(string='TIVET No', copy=False)
    kra_pin = fields.Char(string='KRA PIN', copy=False)
    contract_type = fields.Selection([
        ('permanent', 'Permanent'),
        ('contract', 'Contract'),
        ('part_time', 'Part-time'),
        ('volunteer', 'Volunteer'),
    ])
    qualification = fields.Selection([
        ('certificate', 'Certificate'),
        ('diploma', 'Diploma'),
        ('degree', 'Degree'),
        ('masters', 'Masters'),
        ('other', 'Other'),
    ])
    qualification_details = fields.Char(string='Qualification Details')
    course_ids = fields.Many2many('umtti.course', 'umtti_trainer_course_rel', 'employee_id', 'course_id',
                                  string='Courses Taught')
    timetable_slot_ids = fields.One2many('umtti.timetable.slot', 'trainer_id', string='Timetable')
    weekly_hours = fields.Float(compute='_compute_weekly_hours', string='Weekly Teaching Hours')

    _sql_constraints = [
        ('tivet_no_uniq', 'unique(tivet_no)', 'TIVET No must be unique.'),
    ]

    @api.depends('timetable_slot_ids.duration', 'timetable_slot_ids.active')
    def _compute_weekly_hours(self):
        for rec in self:
            rec.weekly_hours = sum(rec.timetable_slot_ids.filtered('active').mapped('duration'))

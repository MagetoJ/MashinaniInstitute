from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError

WEEKDAYS = [
    ('0', 'Monday'), ('1', 'Tuesday'), ('2', 'Wednesday'),
    ('3', 'Thursday'), ('4', 'Friday'), ('5', 'Saturday'),
]


class UmttiCourse(models.Model):
    _name = 'umtti.course'
    _description = 'UMTTI Course'
    _order = 'name'

    name = fields.Char(required=True)
    code = fields.Char(required=True)
    department = fields.Selection([
        ('tailoring', 'Tailoring & Fashion Design'),
        ('hairdressing', 'Hairdressing'),
        ('beauty', 'Beauty Therapy'),
        ('food_beverage', 'Food & Beverage'),
        ('engineering', 'Engineering'),
        ('other', 'Other'),
    ], required=True, default='other')
    level = fields.Selection([
        ('level_3', 'Level 3 (Artisan)'),
        ('level_4', 'Level 4 (Craft Certificate)'),
        ('level_5', 'Level 5 (Diploma)'),
        ('level_6', 'Level 6 (Higher Diploma)'),
        ('short', 'Short Course'),
    ], default='level_4')
    duration = fields.Integer(default=6)
    duration_unit = fields.Selection([('weeks', 'Weeks'), ('months', 'Months'), ('years', 'Years')],
                                     default='months', required=True)
    entry_requirements = fields.Text()
    description = fields.Text()
    active = fields.Boolean(default=True)

    unit_ids = fields.One2many('umtti.course.unit', 'course_id', string='Units')
    total_hours = fields.Float(compute='_compute_total_hours', store=True)
    equipment_item_ids = fields.One2many('umtti.equipment.item', 'course_id', string='Required Equipment')
    class_ids = fields.One2many('umtti.class', 'course_id', string='Classes')
    class_count = fields.Integer(compute='_compute_class_count')
    student_count = fields.Integer(compute='_compute_student_count')

    _sql_constraints = [('code_uniq', 'unique(code)', 'Course code must be unique.')]

    @api.depends('unit_ids.hours')
    def _compute_total_hours(self):
        for rec in self:
            rec.total_hours = sum(rec.unit_ids.mapped('hours'))

    def _compute_class_count(self):
        data = self.env['umtti.class']._read_group([('course_id', 'in', self.ids)], ['course_id'], ['__count'])
        counts = {course.id: count for course, count in data}
        for rec in self:
            rec.class_count = counts.get(rec.id, 0)

    def _compute_student_count(self):
        data = self.env['res.partner']._read_group(
            [('course_id', 'in', self.ids), ('is_student', '=', True)], ['course_id'], ['__count'])
        counts = {course.id: count for course, count in data}
        for rec in self:
            rec.student_count = counts.get(rec.id, 0)

    @api.constrains('duration')
    def _check_duration(self):
        for rec in self:
            if rec.duration <= 0:
                raise ValidationError(_('Course duration must be greater than zero.'))

    def action_view_classes(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Classes'),
            'res_model': 'umtti.class',
            'view_mode': 'tree,form',
            'domain': [('course_id', '=', self.id)],
            'context': {'default_course_id': self.id},
        }


class UmttiCourseUnit(models.Model):
    """A unit / module of competency within a course."""
    _name = 'umtti.course.unit'
    _description = 'Course Unit'
    _order = 'course_id, sequence, id'

    sequence = fields.Integer(default=10)
    course_id = fields.Many2one('umtti.course', required=True, ondelete='cascade', index=True)
    code = fields.Char()
    name = fields.Char(required=True)
    hours = fields.Float(string='Contact Hours')
    unit_type = fields.Selection([('theory', 'Theory'), ('practical', 'Practical'), ('mixed', 'Theory & Practical')],
                                 default='mixed')

    @api.depends('code', 'name')
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = f'[{rec.code}] {rec.name}' if rec.code else rec.name


class UmttiEquipmentItem(models.Model):
    """Master list of equipment a trainee must bring, per course."""
    _name = 'umtti.equipment.item'
    _description = 'Course Equipment Requirement'
    _order = 'course_id, sequence, id'

    sequence = fields.Integer(default=10)
    course_id = fields.Many2one('umtti.course', required=True, ondelete='cascade')
    name = fields.Char(string='Equipment', required=True)
    quantity = fields.Float(string='Qty Required', default=1.0)


class UmttiClass(models.Model):
    """A cohort / intake of students taking a course together."""
    _name = 'umtti.class'
    _description = 'Class / Intake'
    _order = 'start_date desc, name'

    name = fields.Char(required=True, help='e.g. Tailoring Jan 2026 Intake')
    course_id = fields.Many2one('umtti.course', required=True, index=True)
    start_date = fields.Date()
    end_date = fields.Date()
    state = fields.Selection([('draft', 'Draft'), ('running', 'Running'), ('done', 'Completed')],
                             default='draft', required=True)
    student_ids = fields.Many2many('res.partner', 'umtti_class_student_rel', 'class_id', 'partner_id',
                                   string='Students', domain=[('is_student', '=', True)])
    student_count = fields.Integer(compute='_compute_student_count', store=True)
    slot_ids = fields.One2many('umtti.timetable.slot', 'class_id', string='Timetable')
    active = fields.Boolean(default=True)

    @api.depends('student_ids')
    def _compute_student_count(self):
        for rec in self:
            rec.student_count = len(rec.student_ids)

    @api.constrains('start_date', 'end_date')
    def _check_dates(self):
        for rec in self:
            if rec.start_date and rec.end_date and rec.end_date < rec.start_date:
                raise ValidationError(_('End date cannot be before start date.'))

    def action_start(self):
        self.write({'state': 'running'})

    def action_done(self):
        self.write({'state': 'done'})

    def action_load_course_students(self):
        """Add every enrolled student of this course who isn't in the class yet."""
        for rec in self:
            students = self.env['res.partner'].search([
                ('is_student', '=', True), ('course_id', '=', rec.course_id.id),
                ('student_state', '=', 'enrolled'),
            ])
            rec.student_ids = [(4, s.id) for s in students]


class UmttiTimetableSlot(models.Model):
    """A recurring weekly lesson slot."""
    _name = 'umtti.timetable.slot'
    _description = 'Timetable Slot'
    _order = 'weekday, hour_from'

    class_id = fields.Many2one('umtti.class', required=True, ondelete='cascade', index=True)
    course_id = fields.Many2one(related='class_id.course_id', store=True)
    unit_id = fields.Many2one('umtti.course.unit', required=True, domain="[('course_id', '=', course_id)]")
    trainer_id = fields.Many2one('hr.employee', string='Trainer', required=True,
                                 domain=[('is_trainer', '=', True)], index=True)
    weekday = fields.Selection(WEEKDAYS, required=True, default='0')
    hour_from = fields.Float(string='From', required=True, default=8.0)
    hour_to = fields.Float(string='To', required=True, default=10.0)
    duration = fields.Float(compute='_compute_duration', store=True)
    room = fields.Char(string='Room / Workshop')
    active = fields.Boolean(default=True)

    @api.depends('hour_from', 'hour_to')
    def _compute_duration(self):
        for rec in self:
            rec.duration = max(rec.hour_to - rec.hour_from, 0.0)

    @api.constrains('hour_from', 'hour_to')
    def _check_hours(self):
        for rec in self:
            if not (0 <= rec.hour_from < rec.hour_to <= 24):
                raise ValidationError(_('Slot end time must be after the start time.'))

    @api.constrains('weekday', 'hour_from', 'hour_to', 'trainer_id', 'class_id', 'room', 'active')
    def _check_clashes(self):
        for rec in self.filtered('active'):
            overlap = [
                ('id', '!=', rec.id), ('active', '=', True), ('weekday', '=', rec.weekday),
                ('hour_from', '<', rec.hour_to), ('hour_to', '>', rec.hour_from),
            ]
            if self.search_count(overlap + [('trainer_id', '=', rec.trainer_id.id)]):
                raise ValidationError(_('Trainer %s already has a lesson at that time.', rec.trainer_id.name))
            if self.search_count(overlap + [('class_id', '=', rec.class_id.id)]):
                raise ValidationError(_('Class %s already has a lesson at that time.', rec.class_id.name))
            if rec.room and self.search_count(overlap + [('room', '=ilike', rec.room)]):
                raise ValidationError(_('Room %s is already booked at that time.', rec.room))


class UmttiSchemeWork(models.Model):
    _name = 'umtti.scheme.work'
    _description = 'Scheme of Work'
    _inherit = ['mail.thread']
    _order = 'id desc'

    name = fields.Char(compute='_compute_name', store=True)
    class_id = fields.Many2one('umtti.class', required=True, tracking=True)
    course_id = fields.Many2one(related='class_id.course_id', store=True)
    unit_id = fields.Many2one('umtti.course.unit', required=True, domain="[('course_id', '=', course_id)]")
    trainer_id = fields.Many2one('hr.employee', string='Trainer', required=True,
                                 domain=[('is_trainer', '=', True)])
    academic_year = fields.Char(required=True, default=lambda self: str(fields.Date.today().year))
    term = fields.Selection([('1', 'Term 1'), ('2', 'Term 2'), ('3', 'Term 3')], required=True, default='1')
    state = fields.Selection([('draft', 'Draft'), ('submitted', 'Submitted'), ('approved', 'Approved')],
                             default='draft', tracking=True)
    line_ids = fields.One2many('umtti.scheme.work.line', 'scheme_id', string='Weekly Plan')
    progress = fields.Float(compute='_compute_progress', store=True, string='Coverage %')

    @api.depends('unit_id', 'class_id', 'term', 'academic_year')
    def _compute_name(self):
        for rec in self:
            rec.name = _('%(unit)s - %(cls)s - T%(term)s %(year)s',
                         unit=rec.unit_id.name or '', cls=rec.class_id.name or '',
                         term=rec.term or '', year=rec.academic_year or '')

    @api.depends('line_ids.covered')
    def _compute_progress(self):
        for rec in self:
            total = len(rec.line_ids)
            rec.progress = 100.0 * len(rec.line_ids.filtered('covered')) / total if total else 0.0

    def action_submit(self):
        for rec in self:
            if not rec.line_ids:
                raise ValidationError(_('Add at least one week to the scheme before submitting.'))
        self.write({'state': 'submitted'})

    def action_approve(self):
        if not self.env.user.has_group('umtti_school_management.group_umtti_user'):
            raise AccessError(_('Only officers or managers can approve a scheme of work.'))
        self.write({'state': 'approved'})

    def action_reset(self):
        self.write({'state': 'draft'})


class UmttiSchemeWorkLine(models.Model):
    _name = 'umtti.scheme.work.line'
    _description = 'Scheme of Work Line'
    _order = 'scheme_id, week_no, id'

    scheme_id = fields.Many2one('umtti.scheme.work', required=True, ondelete='cascade', index=True)
    week_no = fields.Integer(string='Week', required=True, default=1)
    lesson_no = fields.Integer(string='Lesson', default=1)
    topic = fields.Char(required=True)
    objectives = fields.Text(string='Learning Outcomes')
    activities = fields.Text(string='Learning Activities')
    resources = fields.Char(string='Resources / References')
    assessment = fields.Char(string='Assessment Method')
    covered = fields.Boolean()
    date_covered = fields.Date()

    @api.onchange('covered')
    def _onchange_covered(self):
        self.date_covered = fields.Date.context_today(self) if self.covered else False


class UmttiAttendanceSheet(models.Model):
    _name = 'umtti.attendance.sheet'
    _description = 'Class Attendance Sheet'
    _inherit = ['mail.thread']
    _order = 'date desc, id desc'

    name = fields.Char(compute='_compute_name', store=True)
    date = fields.Date(required=True, default=fields.Date.context_today)
    class_id = fields.Many2one('umtti.class', required=True, index=True)
    course_id = fields.Many2one(related='class_id.course_id', store=True)
    unit_id = fields.Many2one('umtti.course.unit', required=True, domain="[('course_id', '=', course_id)]")
    trainer_id = fields.Many2one('hr.employee', string='Trainer', required=True,
                                 domain=[('is_trainer', '=', True)],
                                 default=lambda self: self.env['hr.employee'].search(
                                     [('user_id', '=', self.env.uid), ('is_trainer', '=', True)], limit=1))
    state = fields.Selection([('draft', 'Draft'), ('done', 'Confirmed')], default='draft', tracking=True)
    line_ids = fields.One2many('umtti.attendance.line', 'sheet_id', string='Students')
    present_count = fields.Integer(compute='_compute_stats', store=True)
    absent_count = fields.Integer(compute='_compute_stats', store=True)
    attendance_rate = fields.Float(compute='_compute_stats', store=True, string='Attendance %')

    _sql_constraints = [
        ('sheet_uniq', 'unique(class_id, unit_id, date, trainer_id)',
         'An attendance sheet already exists for this class, unit and date.'),
    ]

    @api.depends('class_id', 'unit_id', 'date')
    def _compute_name(self):
        for rec in self:
            rec.name = '%s / %s / %s' % (rec.class_id.name or '', rec.unit_id.name or '', rec.date or '')

    @api.depends('line_ids.status')
    def _compute_stats(self):
        for rec in self:
            total = len(rec.line_ids)
            present = len(rec.line_ids.filtered(lambda l: l.status in ('present', 'late')))
            rec.present_count = present
            rec.absent_count = len(rec.line_ids.filtered(lambda l: l.status == 'absent'))
            rec.attendance_rate = 100.0 * present / total if total else 0.0

    @api.constrains('date')
    def _check_date(self):
        for rec in self:
            if rec.date > fields.Date.context_today(rec):
                raise ValidationError(_('Attendance cannot be recorded for a future date.'))

    def action_load_students(self):
        for rec in self.filtered(lambda r: r.state == 'draft'):
            have = rec.line_ids.mapped('student_id')
            self.env['umtti.attendance.line'].create([
                {'sheet_id': rec.id, 'student_id': s.id, 'status': 'present'}
                for s in rec.class_id.student_ids - have
            ])

    def action_confirm(self):
        for rec in self:
            if not rec.line_ids:
                raise ValidationError(_('Load the students before confirming attendance.'))
        self.write({'state': 'done'})

    def action_reset(self):
        self.write({'state': 'draft'})

    def write(self, vals):
        if self.filtered(lambda r: r.state == 'done') and set(vals) - {'state', 'message_follower_ids'}:
            raise ValidationError(_('Confirmed attendance sheets cannot be edited. Reset to draft first.'))
        return super().write(vals)


class UmttiAttendanceLine(models.Model):
    _name = 'umtti.attendance.line'
    _description = 'Attendance Line'
    _order = 'sheet_id, student_id'

    sheet_id = fields.Many2one('umtti.attendance.sheet', required=True, ondelete='cascade', index=True)
    student_id = fields.Many2one('res.partner', required=True, domain=[('is_student', '=', True)], index=True)
    status = fields.Selection([
        ('present', 'Present'), ('absent', 'Absent'), ('late', 'Late'), ('excused', 'Excused'),
    ], required=True, default='present')
    remarks = fields.Char()
    date = fields.Date(related='sheet_id.date', store=True)
    class_id = fields.Many2one(related='sheet_id.class_id', store=True)
    unit_id = fields.Many2one(related='sheet_id.unit_id', store=True)

    _sql_constraints = [
        ('student_sheet_uniq', 'unique(sheet_id, student_id)', 'Student already listed on this sheet.'),
    ]

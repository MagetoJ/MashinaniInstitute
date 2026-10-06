from collections import defaultdict

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


class UmttiGradeBand(models.Model):
    """Grading scale. The band with the highest min_percent <= score applies."""
    _name = 'umtti.grade.band'
    _description = 'Grade Band'
    _order = 'min_percent desc'

    name = fields.Char(string='Grade', required=True)
    code = fields.Char(required=True)
    min_percent = fields.Float(string='Minimum %', required=True)
    competent = fields.Boolean(default=True, help='Scores in this band count as Competent.')

    _sql_constraints = [('min_uniq', 'unique(min_percent)', 'Two grade bands cannot share the same minimum %.')]

    @api.model
    def band_for(self, percent):
        for band in self.search([], order='min_percent desc'):
            if percent >= band.min_percent:
                return band
        return self.browse()


class UmttiRubric(models.Model):
    _name = 'umtti.rubric'
    _description = 'Assessment Rubric'

    name = fields.Char(required=True)
    description = fields.Text()
    criterion_ids = fields.One2many('umtti.rubric.criterion', 'rubric_id', string='Criteria')
    total_points = fields.Float(compute='_compute_total_points', store=True)

    @api.depends('criterion_ids.max_points')
    def _compute_total_points(self):
        for rec in self:
            rec.total_points = sum(rec.criterion_ids.mapped('max_points'))


class UmttiRubricCriterion(models.Model):
    _name = 'umtti.rubric.criterion'
    _description = 'Rubric Criterion'
    _order = 'rubric_id, sequence, id'

    sequence = fields.Integer(default=10)
    rubric_id = fields.Many2one('umtti.rubric', required=True, ondelete='cascade')
    name = fields.Char(string='Criterion', required=True)
    descriptor = fields.Char(string='What good looks like')
    max_points = fields.Float(required=True, default=5.0)

    @api.constrains('max_points')
    def _check_max_points(self):
        for rec in self:
            if rec.max_points <= 0:
                raise ValidationError(_('Criterion points must be greater than zero.'))


class UmttiAssessment(models.Model):
    _name = 'umtti.assessment'
    _description = 'Assessment / Examination'
    _inherit = ['mail.thread']
    _order = 'date desc, id desc'

    name = fields.Char(required=True)
    assessment_type = fields.Selection([('formative', 'Formative'), ('summative', 'Summative')],
                                       required=True, default='formative', tracking=True)
    class_id = fields.Many2one('umtti.class', required=True, index=True)
    course_id = fields.Many2one(related='class_id.course_id', store=True)
    unit_id = fields.Many2one('umtti.course.unit', required=True, domain="[('course_id', '=', course_id)]")
    trainer_id = fields.Many2one('hr.employee', string='Trainer', required=True,
                                 domain=[('is_trainer', '=', True)],
                                 default=lambda self: self.env['hr.employee'].search(
                                     [('user_id', '=', self.env.uid), ('is_trainer', '=', True)], limit=1))
    date = fields.Date(required=True, default=fields.Date.context_today)
    max_marks = fields.Float(string='Maximum Marks', required=True, default=100.0)
    weight = fields.Float(default=100.0, help='Relative weight when combining assessments into a unit score.')
    rubric_id = fields.Many2one('umtti.rubric', string='Rubric')
    state = fields.Selection([('draft', 'Draft'), ('marking', 'Marking'), ('published', 'Published')],
                             default='draft', tracking=True)
    result_ids = fields.One2many('umtti.assessment.result', 'assessment_id', string='Results')
    average_percent = fields.Float(compute='_compute_stats', store=True, string='Average %')
    pass_rate = fields.Float(compute='_compute_stats', store=True, string='Competent %')

    @api.constrains('max_marks', 'weight')
    def _check_numbers(self):
        for rec in self:
            if rec.max_marks <= 0:
                raise ValidationError(_('Maximum marks must be greater than zero.'))
            if rec.weight < 0:
                raise ValidationError(_('Weight cannot be negative.'))

    @api.depends('result_ids.percentage', 'result_ids.competent', 'result_ids.absent')
    def _compute_stats(self):
        for rec in self:
            sat = rec.result_ids.filtered(lambda r: not r.absent)
            rec.average_percent = sum(sat.mapped('percentage')) / len(sat) if sat else 0.0
            rec.pass_rate = 100.0 * len(sat.filtered('competent')) / len(sat) if sat else 0.0

    def action_load_students(self):
        Result = self.env['umtti.assessment.result']
        for rec in self.filtered(lambda r: r.state != 'published'):
            have = rec.result_ids.mapped('student_id')
            new = Result.create([{'assessment_id': rec.id, 'student_id': s.id}
                                 for s in rec.class_id.student_ids - have])
            if rec.rubric_id:
                new._load_rubric_scores()
            if rec.state == 'draft':
                rec.state = 'marking'

    def action_publish(self):
        for rec in self:
            if not rec.result_ids:
                raise ValidationError(_('Load the students and enter marks before publishing.'))
        self.write({'state': 'published'})

    def action_reset(self):
        if not self.env.user.has_group('umtti_school_management.group_umtti_user'):
            raise AccessError(_('Only officers or managers can reopen a published assessment.'))
        self.write({'state': 'marking'})


class UmttiAssessmentResult(models.Model):
    _name = 'umtti.assessment.result'
    _description = 'Assessment Result'
    _order = 'assessment_id, student_id'

    assessment_id = fields.Many2one('umtti.assessment', required=True, ondelete='cascade', index=True)
    student_id = fields.Many2one('res.partner', required=True, domain=[('is_student', '=', True)], index=True)
    absent = fields.Boolean()
    marks = fields.Float(compute='_compute_marks', store=True, readonly=False)
    percentage = fields.Float(compute='_compute_percentage', store=True)
    grade_id = fields.Many2one('umtti.grade.band', compute='_compute_grade', store=True, string='Grade')
    competent = fields.Boolean(related='grade_id.competent', store=True)
    remarks = fields.Char()
    score_ids = fields.One2many('umtti.assessment.result.score', 'result_id', string='Rubric Scores')

    unit_id = fields.Many2one(related='assessment_id.unit_id', store=True)
    class_id = fields.Many2one(related='assessment_id.class_id', store=True)
    assessment_state = fields.Selection(related='assessment_id.state', store=True)

    _sql_constraints = [
        ('student_assessment_uniq', 'unique(assessment_id, student_id)', 'Student already has a result here.'),
    ]

    @api.depends('score_ids.score', 'assessment_id.max_marks', 'assessment_id.rubric_id.total_points')
    def _compute_marks(self):
        for rec in self:
            if rec.score_ids:
                total = rec.assessment_id.rubric_id.total_points
                rec.marks = sum(rec.score_ids.mapped('score')) / total * rec.assessment_id.max_marks if total else 0.0
            else:
                rec.marks = rec.marks

    @api.depends('marks', 'absent', 'assessment_id.max_marks')
    def _compute_percentage(self):
        for rec in self:
            max_marks = rec.assessment_id.max_marks
            rec.percentage = 0.0 if rec.absent or not max_marks else rec.marks / max_marks * 100.0

    @api.depends('percentage', 'absent')
    def _compute_grade(self):
        Band = self.env['umtti.grade.band']
        for rec in self:
            rec.grade_id = False if rec.absent else Band.band_for(rec.percentage)

    @api.constrains('marks')
    def _check_marks(self):
        for rec in self:
            if rec.marks < 0 or rec.marks > rec.assessment_id.max_marks + 1e-6:
                raise ValidationError(_('Marks for %(student)s must be between 0 and %(max)s.',
                                        student=rec.student_id.name, max=rec.assessment_id.max_marks))

    def _check_unlocked(self, vals=None):
        locked = self.filtered(lambda r: r.assessment_id.state == 'published')
        if locked and (vals is None or {'marks', 'absent', 'remarks', 'score_ids'} & set(vals)):
            raise ValidationError(_('Results of a published assessment are locked. Reopen the assessment first.'))

    def write(self, vals):
        self._check_unlocked(vals)
        return super().write(vals)

    def unlink(self):
        self._check_unlocked()
        return super().unlink()

    def _load_rubric_scores(self):
        Score = self.env['umtti.assessment.result.score']
        for rec in self:
            have = rec.score_ids.mapped('criterion_id')
            Score.create([{'result_id': rec.id, 'criterion_id': c.id}
                          for c in rec.assessment_id.rubric_id.criterion_ids - have])

    def action_open_scores(self):
        self.ensure_one()
        self._load_rubric_scores()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Rubric Scores'),
            'res_model': 'umtti.assessment.result',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }


class UmttiAssessmentResultScore(models.Model):
    _name = 'umtti.assessment.result.score'
    _description = 'Rubric Score'
    _order = 'result_id, criterion_id'

    result_id = fields.Many2one('umtti.assessment.result', required=True, ondelete='cascade', index=True)
    criterion_id = fields.Many2one('umtti.rubric.criterion', required=True)
    max_points = fields.Float(related='criterion_id.max_points')
    score = fields.Float()

    @api.constrains('score')
    def _check_score(self):
        for rec in self:
            if rec.score < 0 or rec.score > rec.criterion_id.max_points:
                raise ValidationError(_('Score for "%(c)s" must be between 0 and %(m)s.',
                                        c=rec.criterion_id.name, m=rec.criterion_id.max_points))


class ResPartner(models.Model):
    _inherit = 'res.partner'

    result_ids = fields.One2many('umtti.assessment.result', 'student_id', string='Assessment Results')
    poe_ids = fields.One2many('umtti.poe', 'student_id', string='Portfolios of Evidence')
    tool_issue_ids = fields.One2many('umtti.tool.issue', 'student_id', string='Tools Issued')

    def get_transcript_data(self):
        """Plain-data transcript (published, attended assessments only), used by the QWeb report."""
        self.ensure_one()
        Band = self.env['umtti.grade.band']
        results = self.env['umtti.assessment.result'].sudo().search([
            ('student_id', '=', self.id), ('assessment_state', '=', 'published'), ('absent', '=', False)])
        by_unit = defaultdict(list)
        for res in results:
            by_unit[res.unit_id].append(res)
        units = []
        for unit in sorted(by_unit, key=lambda u: (u.sequence, u.id)):
            rows = by_unit[unit]
            weight = sum(r.assessment_id.weight for r in rows)
            score = (sum(r.percentage * r.assessment_id.weight for r in rows) / weight if weight
                     else sum(r.percentage for r in rows) / len(rows))
            band = Band.band_for(score)
            units.append({
                'unit_id': unit.id,
                'unit': unit.display_name,
                'score': round(score, 1),
                'grade': band.name or '-',
                'competent': bool(band.competent),
                'results': [{
                    'assessment': r.assessment_id.name,
                    'type': dict(r.assessment_id._fields['assessment_type'].selection)[r.assessment_id.assessment_type],
                    'date': r.assessment_id.date,
                    'marks': r.marks,
                    'max_marks': r.assessment_id.max_marks,
                    'percentage': round(r.percentage, 1),
                    'grade': r.grade_id.code or '-',
                } for r in sorted(rows, key=lambda r: r.assessment_id.date)],
            })
        overall = sum(u['score'] for u in units) / len(units) if units else 0.0
        band = Band.band_for(overall) if units else Band.browse()
        return {
            'units': units,
            'overall': round(overall, 1),
            'grade': band.name or '-',
            'competent': bool(units) and all(u['competent'] for u in units),
        }

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger


@tagged('post_install', '-at_install', 'umtti')
class TestUmttiLifecycle(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = cls.env['umtti.course'].create({
            'name': 'Test Tailoring', 'code': 'T-TLR', 'department': 'tailoring',
            'unit_ids': [(0, 0, {'name': 'Pattern Making', 'code': 'U1', 'hours': 40}),
                         (0, 0, {'name': 'Garment Construction', 'code': 'U2', 'hours': 60})],
            'equipment_item_ids': [(0, 0, {'name': 'Scissors', 'quantity': 1}),
                                   (0, 0, {'name': 'Tape measure', 'quantity': 2})],
        })
        cls.u1, cls.u2 = cls.course.unit_ids
        cls.student = cls.env['res.partner'].create({
            'name': 'Amina Test', 'is_student': True, 'id_no': 'T-1001',
            'course_id': cls.course.id, 'date_of_birth': '2004-05-01'})
        cls.student2 = cls.env['res.partner'].create({
            'name': 'Brian Test', 'is_student': True, 'id_no': 'T-1002', 'course_id': cls.course.id})
        cls.trainer = cls.env['hr.employee'].create({
            'name': 'Trainer One', 'is_trainer': True, 'tivet_no': 'T-TV-1'})
        cls.klass = cls.env['umtti.class'].create({'name': 'Test Class', 'course_id': cls.course.id})
        cls.klass.student_ids = [(6, 0, (cls.student | cls.student2).ids)]

    # ---- admission
    def test_admission_number_checklists_and_age(self):
        self.assertTrue(self.student.admission_no.startswith('UMTTI/'))
        self.assertGreater(self.student.age, 0)
        self.assertEqual(len(self.student.document_check_ids), 6)
        self.assertEqual(len(self.student.equipment_line_ids), 2)

    def test_enrol_requires_documents(self):
        with self.assertRaises(ValidationError):
            self.student.action_enroll()
        self.student.document_check_ids.write({'received': True})
        self.student.action_enroll()
        self.assertEqual(self.student.student_state, 'enrolled')

    def test_student_validations(self):
        with self.assertRaises(ValidationError):
            self.env['res.partner'].create({'name': 'Dup', 'is_student': True, 'id_no': 'T-1001'})
        with self.assertRaises(ValidationError):
            self.env['res.partner'].create({'name': 'Future', 'is_student': True, 'date_of_birth': '2999-01-01'})

    @mute_logger('odoo.sql_db')
    def test_tivet_no_unique(self):
        with self.assertRaises(Exception), self.cr.savepoint():
            self.env['hr.employee'].create({'name': 'T2', 'is_trainer': True, 'tivet_no': 'T-TV-1'})

    # ---- academics
    def test_timetable_clash(self):
        Slot = self.env['umtti.timetable.slot']
        Slot.create({'class_id': self.klass.id, 'unit_id': self.u1.id, 'trainer_id': self.trainer.id,
                     'weekday': '0', 'hour_from': 8, 'hour_to': 10, 'room': 'W1'})
        other = self.env['umtti.class'].create({'name': 'Other', 'course_id': self.course.id})
        with self.assertRaises(ValidationError):
            Slot.create({'class_id': other.id, 'unit_id': self.u1.id, 'trainer_id': self.trainer.id,
                         'weekday': '0', 'hour_from': 9, 'hour_to': 11})
        self.assertEqual(self.trainer.weekly_hours, 2.0)

    def test_attendance_sheet(self):
        sheet = self.env['umtti.attendance.sheet'].create({
            'class_id': self.klass.id, 'unit_id': self.u1.id, 'trainer_id': self.trainer.id})
        sheet.action_load_students()
        sheet.line_ids[0].status = 'absent'
        sheet.action_confirm()
        self.assertEqual((sheet.present_count, sheet.absent_count, sheet.attendance_rate), (1, 1, 50.0))
        with self.assertRaises(ValidationError):
            sheet.write({'date': '2020-01-01'})

    # ---- exams
    def _assessment(self, name, kind, unit, max_marks, weight, **kw):
        a = self.env['umtti.assessment'].create({
            'name': name, 'assessment_type': kind, 'class_id': self.klass.id, 'unit_id': unit.id,
            'trainer_id': self.trainer.id, 'max_marks': max_marks, 'weight': weight, **kw})
        a.action_load_students()
        return a

    def _result(self, assessment, student=None):
        return assessment.result_ids.filtered(lambda r: r.student_id == (student or self.student))

    def test_grading_transcript_and_locking(self):
        a1 = self._assessment('CAT 1', 'formative', self.u1, 20, 30)
        a2 = self._assessment('Final', 'summative', self.u1, 100, 70)
        self._result(a1).marks = 18
        self._result(a2).marks = 40
        self.assertEqual(self._result(a1).grade_id.name, 'Distinction')
        with self.assertRaises(ValidationError):
            self._result(a1).marks = 25
        a1.action_publish()
        a2.action_publish()
        with self.assertRaises(ValidationError):
            self._result(a1).marks = 10
        unit = self.student.get_transcript_data()['units'][0]
        self.assertEqual(unit['score'], 55.0)       # (90*30 + 40*70) / 100
        self.assertEqual(unit['grade'], 'Pass')

    def test_rubric_scoring(self):
        rubric = self.env['umtti.rubric'].create({'name': 'Sewing', 'criterion_ids': [
            (0, 0, {'name': 'Seams', 'max_points': 5}), (0, 0, {'name': 'Finish', 'max_points': 5})]})
        a = self._assessment('Practical', 'summative', self.u2, 100, 100, rubric_id=rubric.id)
        res = self._result(a)
        res.score_ids[0].score, res.score_ids[1].score = 4, 3
        self.assertEqual(res.marks, 70.0)

    # ---- PoE + graduation
    def _make_competent(self):
        a = self._assessment('Unit1', 'summative', self.u1, 100, 100)
        b = self._assessment('Unit2', 'summative', self.u2, 100, 100)
        for x in (a, b):
            self._result(x).marks = 80
            x.action_publish()
        for unit in (self.u1, self.u2):
            poe = self.env['umtti.poe'].create({'student_id': self.student.id, 'unit_id': unit.id})
            entry = self.env['umtti.poe.entry'].create({'poe_id': poe.id, 'title': 'Sample'})
            if unit == self.u1:
                poe.action_submit()
                with self.assertRaises(ValidationError):
                    poe.action_verify()
            else:
                poe.action_submit()
            entry.action_verify()
            poe.action_verify()

    def test_graduation(self):
        self._make_competent()
        grad = self.env['umtti.graduation'].create({'name': 'Grad', 'class_id': self.klass.id})
        grad.action_generate_lines()
        lines = {l.student_id: l for l in grad.line_ids}
        self.assertTrue(lines[self.student].eligible)
        self.assertFalse(lines[self.student2].eligible)
        with self.assertRaises(AccessError):                     # superuser is not in the Manager group
            grad.action_process()
        grad.with_user(self.env.ref('base.user_admin')).action_process()
        self.assertEqual(lines[self.student].decision, 'graduated')
        self.assertTrue(lines[self.student].certificate_no.startswith('UMTTI/CERT/'))
        self.assertEqual(lines[self.student2].decision, 'withheld')
        self.assertEqual(self.student.student_state, 'completed')

    def test_transcript_report_renders(self):
        self._make_competent()
        html = self.env['ir.actions.report']._render_qweb_html(
            'umtti_school_management.report_transcript_doc', self.student.ids)[0].decode()
        self.assertIn('Academic Transcript', html)
        self.assertIn('Pattern Making', html)

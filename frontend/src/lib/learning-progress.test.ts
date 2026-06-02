import { describe, expect, it } from 'vitest';

import {
  buildLearningProgressMessage,
  getLearningProgressReadiness,
  normalizeLearningProgressSnapshot,
} from '@/lib/learning-progress';

describe('learning progress helpers', () => {
  it('normalizes student progress and breakthrough state', () => {
    const snapshot = normalizeLearningProgressSnapshot({
      userInfo: { role: 'student' },
      classOfferingId: 9,
      learningProgress: {
        score: 82.35,
        progress_percent: 82,
        current_level: { level_name: '筑基', short_name: '筑基' },
        eligible_stage: { key: 'foundation', short_name: '筑基' },
        certificates: [{ id: 1 }, { id: 2 }],
        metrics: { components: { material: 18, task: 42, interaction: 10 } },
        class_position: { current: { rank: 3, top_percent: 20 } },
        stages: [{ key: 'foundation', short_name: '筑基', status: 'challenge_ready', progress_percent: 100 }],
      },
    });

    expect(snapshot.mode).toBe('student');
    expect(snapshot.score).toBe(82.4);
    expect(snapshot.primaryAction).toBe('start-stage-exam');
    expect(snapshot.metrics.find((item) => item.label === '证书')?.value).toBe(2);
    expect(getLearningProgressReadiness(snapshot)).toBe(82);
    expect(buildLearningProgressMessage(snapshot)).toContain('破境条件');
  });

  it('normalizes teacher overview and attention message', () => {
    const snapshot = normalizeLearningProgressSnapshot({
      userInfo: { role: 'teacher' },
      classOfferingId: 9,
      learningOverview: {
        student_count: 40,
        active_student_count: 33,
        quiet_student_count: 7,
        need_attention_count: 5,
        average_score: 76.6,
        average_material_percent: 70,
        average_task_percent: 64,
        personal_stage_exam_stats: { active_count: 2, total_count: 9 },
        distribution: [{ key: 'foundation', name: '筑基', count: 4 }],
      },
    });

    expect(snapshot.mode).toBe('teacher');
    expect(snapshot.subtitle).toContain('40 名学生');
    expect(snapshot.metrics.find((item) => item.label === '待关注')?.value).toBe(5);
    expect(getLearningProgressReadiness(snapshot)).toBe(70);
    expect(buildLearningProgressMessage(snapshot)).toContain('需要关注');
  });
});

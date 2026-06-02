import { CheckCircle2, Circle } from 'lucide-react';
import { useEffect, useMemo } from 'react';

import { readIslandJsonPayload } from '@/lib/island-payload';
import { mountReactIslandsWhenReady } from '@/lib/mount-react-island';
import {
  collectSubmissionJumpGroups,
  SUBMISSION_JUMP_MANAGED_ATTR,
  summarizeSubmissionJumpGroups,
  truncateJumpText,
  type SubmissionJumpPayload,
} from '@/lib/submission-jump-nav';

function scrollToSubmissionTarget(targetId: string) {
  const target = document.getElementById(targetId);
  if (!target) {
    return;
  }
  target.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function SubmissionJumpNavIsland(payload: SubmissionJumpPayload) {
  const groups = useMemo(() => collectSubmissionJumpGroups(payload), [payload]);
  const summary = useMemo(() => summarizeSubmissionJumpGroups(groups), [groups]);

  useEffect(() => {
    const host = document.getElementById('submission-jump-question-groups');
    const answerCount = document.getElementById('submission-jump-answer-count');
    if (host) {
      host.dataset[SUBMISSION_JUMP_MANAGED_ATTR] = 'react';
    }
    if (answerCount) {
      answerCount.textContent = `${summary.answered}/${summary.total}`;
    }

    return () => {
      host?.removeAttribute('data-submission-jump-managed');
    };
  }, [summary.answered, summary.total]);

  if (!groups.length) {
    return null;
  }

  return (
    <>
      {groups.map((group) => {
        const answered = group.items.filter((item) => item.answered).length;
        return (
          <div className="submission-jump-question-group" key={group.title}>
            <div className="submission-jump-group-title">
              <span>{group.title}</span>
              <span>{answered}/{group.items.length}</span>
            </div>
            <div className="submission-jump-question-list">
              {group.items.map((item) => (
                <button
                  className={`submission-jump-question ${item.answered ? 'is-answered' : ''}`}
                  data-jump-question={`submission-q-${item.index}`}
                  key={`${item.index}-${item.id}`}
                  onClick={() => scrollToSubmissionTarget(`submission-q-${item.index}`)}
                  type="button"
                >
                  <span className="submission-jump-number">{item.index}</span>
                  <span className="submission-jump-copy">{truncateJumpText(item.text) || `第 ${item.index} 题`}</span>
                  <span className="submission-jump-state" aria-hidden="true">
                    {item.answered ? <CheckCircle2 size={13} /> : <Circle size={12} />}
                  </span>
                </button>
              ))}
            </div>
          </div>
        );
      })}
    </>
  );
}

mountReactIslandsWhenReady({
  islandName: 'submission-jump-nav',
  defaultMountIdPrefix: 'submission-jump-nav',
  getProps: (mountPoint) => {
    const payload = readIslandJsonPayload(mountPoint, '[data-submission-jump-nav-payload]');
    const record = payload && typeof payload === 'object' ? (payload as Record<string, unknown>) : {};
    return {
      answers: record.answers,
      examQuestions: record.examQuestions,
    };
  },
  render: (props) => <SubmissionJumpNavIsland {...props} />,
});

import { Injectable, Logger } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { RawExtraction } from '@/modules/upload/entities/raw-extraction.entity';
import { ConfidenceScore, ReviewAction, ValidationResult } from './pipeline.types';

/**
 * Confidence thresholds from Architecture §3.2
 *
 * ≥ 0.90  → AUTO_IMPORT
 * 0.75–0.89 → AUTO_IMPORT_FLAG  (imported + flagged for spot check)
 * 0.50–0.74 → HOLD_FOR_REVIEW   (consultant must approve before import)
 * < 0.50   → REJECT             (notify user, do not import)
 */
const THRESHOLD_AUTO_IMPORT    = 0.90;
const THRESHOLD_AUTO_FLAG      = 0.75;
const THRESHOLD_HOLD           = 0.50;

/**
 * Stage 6 — Human review gate
 *
 * Applies confidence thresholds to decide the routing action and persists
 * the raw extraction payload to the `raw_extraction` table for the
 * GoBD audit trail and the consultant review UI.
 */
@Injectable()
export class ReviewGateService {
  private readonly logger = new Logger(ReviewGateService.name);

  constructor(
    @InjectRepository(RawExtraction)
    private readonly rawExtractionRepo: Repository<RawExtraction>,
  ) {}

  /**
   * Determine routing action.
   * Validation errors always result in HOLD_FOR_REVIEW (never auto-import broken data).
   */
  decide(confidence: ConfidenceScore, validation: ValidationResult): ReviewAction {
    // Validation errors override confidence thresholds
    if (!validation.valid) {
      this.logger.warn({
        event: 'review_gate_validation_failed',
        errors: validation.errors,
        action: ReviewAction.HOLD_FOR_REVIEW,
      });
      return ReviewAction.HOLD_FOR_REVIEW;
    }

    let action: ReviewAction;

    if (confidence.overall >= THRESHOLD_AUTO_IMPORT) {
      action = ReviewAction.AUTO_IMPORT;
    } else if (confidence.overall >= THRESHOLD_AUTO_FLAG) {
      action = ReviewAction.AUTO_IMPORT_FLAG;
    } else if (confidence.overall >= THRESHOLD_HOLD) {
      action = ReviewAction.HOLD_FOR_REVIEW;
    } else {
      action = ReviewAction.REJECT;
    }

    this.logger.log({
      event: 'review_gate_decision',
      confidence: confidence.overall,
      direct_ocr_ratio: confidence.direct_ocr_ratio,
      completeness_ratio: confidence.completeness_ratio,
      action,
    });

    return action;
  }

  /**
   * Persist raw extraction payload to `raw_extraction` (GoBD audit trail).
   * Always called regardless of review action.
   */
  async persistRawExtraction(opts: {
    tenantId: string;
    uploadId: string;
    payload: unknown;
    confidence: number;
    issues: string[];
  }): Promise<void> {
    try {
      await this.rawExtractionRepo.save({
        tenant_id: opts.tenantId,
        upload_id: opts.uploadId,
        doc_type: 'invoice',
        extractor: 'vision-pipeline-v2',
        confidence: opts.confidence,
        payload: opts.payload as object,
        issues: opts.issues,
        normalized: false,
      });
    } catch (error) {
      // Non-fatal — audit trail failure must not block the import
      this.logger.error({
        event: 'raw_extraction_persist_failed',
        error: (error as Error).message,
        upload_id: opts.uploadId,
      });
    }
  }
}

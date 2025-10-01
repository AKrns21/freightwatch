/**
 * DTO for updating a project
 */
export class UpdateProjectDto {
  name?: string;
  customer_name?: string;
  phase?: 'quick_check' | 'deep_dive' | 'final_report';
  status?: 'draft' | 'in_progress' | 'review' | 'completed' | 'archived';
  consultant_id?: string;
  metadata?: Record<string, any>;
}

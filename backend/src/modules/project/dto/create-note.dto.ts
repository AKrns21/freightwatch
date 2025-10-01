/**
 * DTO for creating a consultant note
 */
export class CreateNoteDto {
  note_type: 'data_quality' | 'missing_info' | 'action_item' | 'clarification' | 'observation';
  content: string;
  related_to_upload_id?: string;
  related_to_shipment_id?: string;
  priority?: 'low' | 'medium' | 'high' | 'critical';
  status?: 'open' | 'in_progress' | 'resolved' | 'closed';
}

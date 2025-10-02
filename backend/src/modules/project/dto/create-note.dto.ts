import { IsString, IsOptional, IsEnum, IsUUID, IsNotEmpty, MaxLength } from 'class-validator';

/**
 * DTO for creating a consultant note
 */
export class CreateNoteDto {
  @IsEnum(['data_quality', 'missing_info', 'action_item', 'clarification', 'observation'])
  @IsNotEmpty()
  note_type: 'data_quality' | 'missing_info' | 'action_item' | 'clarification' | 'observation';

  @IsString()
  @IsNotEmpty()
  @MaxLength(5000)
  content: string;

  @IsUUID()
  @IsOptional()
  related_to_upload_id?: string;

  @IsUUID()
  @IsOptional()
  related_to_shipment_id?: string;

  @IsEnum(['low', 'medium', 'high', 'critical'])
  @IsOptional()
  priority?: 'low' | 'medium' | 'high' | 'critical';

  @IsEnum(['open', 'in_progress', 'resolved', 'closed'])
  @IsOptional()
  status?: 'open' | 'in_progress' | 'resolved' | 'closed';
}

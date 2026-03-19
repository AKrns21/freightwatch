import { IsString, IsOptional, IsEnum, IsUUID, MaxLength, IsObject } from 'class-validator';

/**
 * DTO for updating a project
 */
export class UpdateProjectDto {
  @IsString()
  @IsOptional()
  @MaxLength(255)
  name?: string;

  @IsString()
  @IsOptional()
  @MaxLength(255)
  customer_name?: string;

  @IsEnum(['quick_check', 'deep_dive', 'final_report'])
  @IsOptional()
  phase?: 'quick_check' | 'deep_dive' | 'final_report';

  @IsEnum(['draft', 'in_progress', 'review', 'completed', 'archived'])
  @IsOptional()
  status?: 'draft' | 'in_progress' | 'review' | 'completed' | 'archived';

  @IsUUID()
  @IsOptional()
  consultant_id?: string;

  @IsObject()
  @IsOptional()
  metadata?: Record<string, any>;
}

import { Injectable, Logger } from '@nestjs/common';
import Anthropic from '@anthropic-ai/sdk';
import {
  LlmParseResult,
  LlmPromptContext,
  AnalysisOptions,
  FileType,
  ColumnMapping,
  DataQualityIssue,
} from '../types/llm-parser.types';

/**
 * LlmParserService - AI-powered file analysis
 *
 * Uses Claude to analyze unknown file formats and suggest mappings.
 * Learns from consultant corrections to improve future suggestions.
 */
@Injectable()
export class LlmParserService {
  private readonly logger = new Logger(LlmParserService.name);
  private anthropic: Anthropic;

  constructor() {
    const apiKey = process.env.ANTHROPIC_API_KEY;

    if (!apiKey) {
      this.logger.warn('ANTHROPIC_API_KEY not set - LLM parsing will be disabled');
    }

    this.anthropic = new Anthropic({
      apiKey: apiKey || 'dummy-key',
    });
  }

  /**
   * Analyze unknown file and suggest mappings
   */
  async analyzeFile(
    fileBuffer: Buffer,
    context: LlmPromptContext,
    options?: AnalysisOptions
  ): Promise<LlmParseResult> {
    this.logger.log({
      event: 'llm_analysis_start',
      filename: context.filename,
      mime_type: context.mime_type,
    });

    try {
      // Extract content from buffer
      const content = await this.extractContent(fileBuffer, context.mime_type);

      // Build analysis prompt
      const prompt = this.buildAnalysisPrompt(content, context, options);

      // Call Claude API
      const response = await this.anthropic.messages.create({
        model: 'claude-sonnet-4-20250514',
        max_tokens: options?.max_tokens || 4000,
        temperature: options?.temperature || 0,
        messages: [{
          role: 'user',
          content: prompt,
        }],
      });

      // Parse response
      const result = this.parseAnalysisResponse(response, content);

      this.logger.log({
        event: 'llm_analysis_complete',
        filename: context.filename,
        file_type: result.file_type,
        confidence: result.confidence,
        mappings_count: result.column_mappings.length,
        issues_count: result.issues.length,
      });

      return result;

    } catch (error) {
      this.logger.error({
        event: 'llm_analysis_error',
        filename: context.filename,
        error: error.message,
      });

      throw error;
    }
  }

  /**
   * Extract text content from file buffer
   */
  private async extractContent(buffer: Buffer, mimeType: string): Promise<string> {
    // CSV or plain text
    if (mimeType.includes('csv') || mimeType.includes('text')) {
      return buffer.toString('utf-8');
    }

    // Excel files
    if (mimeType.includes('excel') || mimeType.includes('spreadsheet')) {
      try {
        const XLSX = require('xlsx');
        const workbook = XLSX.read(buffer);
        const sheet = workbook.Sheets[workbook.SheetNames[0]];
        return XLSX.utils.sheet_to_csv(sheet);
      } catch (error) {
        this.logger.warn('Failed to parse Excel file, falling back to raw buffer');
        return buffer.toString('utf-8', 0, 5000);
      }
    }

    // PDF files
    if (mimeType.includes('pdf')) {
      try {
        const pdfParse = require('pdf-parse');
        const data = await pdfParse(buffer);
        return data.text;
      } catch (error) {
        this.logger.warn('Failed to parse PDF file');
        throw new Error('PDF parsing not available');
      }
    }

    // Fallback: return first 5000 chars as UTF-8
    return buffer.toString('utf-8', 0, 5000);
  }

  /**
   * Build analysis prompt for Claude
   */
  private buildAnalysisPrompt(
    content: string,
    context: LlmPromptContext,
    options?: AnalysisOptions
  ): string {
    const preview = content.substring(0, 2000);
    const sampleSize = options?.sample_size || 3;

    let prompt = `You are analyzing a freight/logistics data file for cost analysis.

**File Information:**
- Filename: ${context.filename}
- MIME type: ${context.mime_type}

**Content Preview (first 2000 chars):**
\`\`\`
${preview}
\`\`\`
`;

    // Add tenant context if available
    if (context.tenant_context) {
      prompt += `\n**Tenant Context:**\n`;

      if (context.tenant_context.known_carriers?.length) {
        prompt += `- Known carriers: ${context.tenant_context.known_carriers.join(', ')}\n`;
      }

      if (context.tenant_context.expected_fields?.length) {
        prompt += `- Expected fields: ${context.tenant_context.expected_fields.join(', ')}\n`;
      }

      if (context.tenant_context.currency) {
        prompt += `- Currency: ${context.tenant_context.currency}\n`;
      }

      if (context.tenant_context.country) {
        prompt += `- Country: ${context.tenant_context.country}\n`;
      }
    }

    prompt += `

**Your Task:**
Analyze this file and provide a structured JSON response with:

1. **File Type Classification**
   - \`shipment_list\`: List of shipments with origin, destination, weight, cost
   - \`invoice\`: Carrier invoice with line items and charges
   - \`tariff_table\`: Carrier pricing table (zones, weight bands, rates)
   - \`route_documentation\`: Route planning or fleet data
   - \`unknown\`: Cannot determine type

2. **Column Mappings**
   For each column, suggest mapping to database fields:
   - Common fields: \`date\`, \`carrier_name\`, \`origin_zip\`, \`dest_zip\`, \`weight_kg\`, \`actual_cost\`, \`service_level\`, \`reference_number\`
   - Include ${sampleSize} sample values for each mapping
   - Provide confidence score (0.0 - 1.0)
   - Note any transformation patterns needed (e.g., date format conversion)

3. **Data Quality Issues**
   Identify problems like:
   - Missing critical data
   - Invalid formats
   - Inconsistent values
   - Ambiguous content

4. **Suggested Actions**
   What should the consultant do next?

**Output Format (JSON only, no markdown):**
\`\`\`json
{
  "file_type": "shipment_list" | "invoice" | "tariff_table" | "route_documentation" | "unknown",
  "confidence": 0.0-1.0,
  "description": "Brief description of the file",
  "column_mappings": [
    {
      "column": "Column name or letter (A, B, C... or actual name)",
      "field": "database_field_name",
      "confidence": 0.0-1.0,
      "pattern": "Transformation needed (optional)",
      "sample_values": ["val1", "val2", "val3"],
      "data_type": "string|number|date"
    }
  ],
  "tariff_structure": {
    "carrier": "Carrier name if identified",
    "currency": "EUR|CHF|USD",
    "lane_type": "domestic_de|de_to_ch|...",
    "zones": [1, 2, 3, ...],
    "weight_bands": [{"min": 0, "max": 50}, ...],
    "has_diesel_surcharge": true|false,
    "has_toll": true|false
  },
  "issues": [
    {
      "type": "missing_data|invalid_format|inconsistent|ambiguous",
      "severity": "low|medium|high|critical",
      "description": "Description of issue",
      "affected_rows": [1, 5, 10],
      "suggested_fix": "How to fix it"
    }
  ],
  "suggested_actions": [
    "Action 1",
    "Action 2"
  ]
}
\`\`\`

**Important Guidelines:**
- Only suggest mappings with confidence >= 0.7
- Be conservative - it's better to flag for review than guess incorrectly
- For tariff tables, identify structure carefully (zones, weight bands)
- Note currency and date formats explicitly
- Identify the carrier if possible
`;

    return prompt;
  }

  /**
   * Parse Claude's analysis response
   */
  private parseAnalysisResponse(
    response: Anthropic.Message,
    originalContent: string
  ): LlmParseResult {
    // Extract text content from response
    const textContent = response.content.find(c => c.type === 'text');

    if (!textContent || textContent.type !== 'text') {
      throw new Error('No text response from LLM');
    }

    const rawText = textContent.text;

    // Try to extract JSON from markdown code blocks
    const jsonMatch = rawText.match(/```json\n([\s\S]*?)\n```/) ||
                      rawText.match(/```\n([\s\S]*?)\n```/);

    const jsonText = jsonMatch ? jsonMatch[1] : rawText;

    try {
      const analysis = JSON.parse(jsonText);

      // Validate and normalize the response
      return {
        file_type: analysis.file_type || 'unknown',
        confidence: analysis.confidence || 0.0,
        description: analysis.description || '',
        column_mappings: this.normalizeColumnMappings(analysis.column_mappings || []),
        tariff_structure: analysis.tariff_structure,
        issues: this.normalizeIssues(analysis.issues || []),
        suggested_actions: analysis.suggested_actions || [],
        needs_review: analysis.confidence < 0.85 || (analysis.issues || []).length > 0,
        raw_analysis: rawText,
      };

    } catch (error) {
      this.logger.error({
        event: 'llm_response_parse_error',
        error: error.message,
        raw_response: rawText.substring(0, 500),
      });

      // Return a safe fallback result
      return {
        file_type: 'unknown',
        confidence: 0.0,
        description: 'Failed to parse LLM response',
        column_mappings: [],
        issues: [{
          type: 'ambiguous',
          severity: 'critical',
          description: 'LLM analysis failed - manual review required',
        }],
        suggested_actions: ['Manually review and map columns'],
        needs_review: true,
        raw_analysis: rawText,
      };
    }
  }

  /**
   * Normalize column mappings from LLM response
   */
  private normalizeColumnMappings(mappings: any[]): ColumnMapping[] {
    return mappings
      .filter(m => m.confidence >= 0.7) // Only keep confident mappings
      .map(m => ({
        column: m.column || '',
        field: m.field || '',
        confidence: parseFloat(m.confidence) || 0.0,
        pattern: m.pattern,
        sample_values: Array.isArray(m.sample_values) ? m.sample_values : [],
        data_type: m.data_type,
      }));
  }

  /**
   * Normalize data quality issues from LLM response
   */
  private normalizeIssues(issues: any[]): DataQualityIssue[] {
    return issues.map(i => ({
      type: i.type || 'ambiguous',
      severity: i.severity || 'medium',
      description: i.description || '',
      affected_rows: Array.isArray(i.affected_rows) ? i.affected_rows : undefined,
      suggested_fix: i.suggested_fix,
    }));
  }

  /**
   * Check if LLM parsing is available
   */
  isAvailable(): boolean {
    return !!process.env.ANTHROPIC_API_KEY;
  }
}

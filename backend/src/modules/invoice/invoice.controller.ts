import {
  Controller,
  Get,
  Post,
  Body,
  Query,
  Param,
  UseGuards,
  Req,
  NotFoundException,
} from '@nestjs/common';
import { InvoiceParserService } from './invoice-parser.service';
import { InvoiceMatcherService } from './invoice-matcher.service';
import { JwtAuthGuard } from '../auth/guards/jwt-auth.guard';

/**
 * InvoiceController - Invoice API Endpoints
 *
 * Endpoints:
 * - POST /invoices/parse - Parse invoice PDF
 * - POST /invoices/match - Match invoice lines to shipments
 * - POST /invoices/manual-match - Manually match line to shipment
 * - POST /invoices/unmatch - Remove match
 * - GET /invoices/matching-stats - Get matching statistics
 * - GET /invoices/:id - Get invoice details
 */
@Controller('invoices')
@UseGuards(JwtAuthGuard)
export class InvoiceController {
  constructor(
    private readonly parserService: InvoiceParserService,
    private readonly matcherService: InvoiceMatcherService,
  ) {}

  /**
   * Parse invoice PDF
   * POST /invoices/parse
   */
  @Post('parse')
  async parse(
    @Body()
    body: {
      fileBuffer: string; // Base64 encoded
      filename: string;
      carrier_id?: string;
      upload_id?: string;
      project_id?: string;
    },
    @Req() req: any,
  ) {
    const tenantId = req.user.tenant_id;

    // Decode base64 buffer
    const fileBuffer = Buffer.from(body.fileBuffer, 'base64');

    // Parse invoice
    const parseResult = await this.parserService.parseInvoicePdf(
      fileBuffer,
      {
        filename: body.filename,
        carrier_id: body.carrier_id,
        tenant_id: tenantId,
        upload_id: body.upload_id,
        project_id: body.project_id,
      },
    );

    // Import into database
    const invoice = await this.parserService.importInvoice(
      parseResult,
      tenantId,
      body.upload_id,
      body.project_id,
    );

    return {
      success: true,
      data: {
        invoice,
        parse_result: parseResult,
      },
    };
  }

  /**
   * Match invoice lines to shipments
   * POST /invoices/match?invoiceId=xxx
   */
  @Post('match')
  async match(
    @Query('invoiceId') invoiceId: string,
    @Query('projectId') projectId: string | undefined,
    @Req() req: any,
  ) {
    const tenantId = req.user.tenant_id;

    const stats = await this.matcherService.matchInvoiceLines(
      invoiceId,
      tenantId,
      projectId,
    );

    return {
      success: true,
      data: stats,
    };
  }

  /**
   * Manually match invoice line to shipment
   * POST /invoices/manual-match
   */
  @Post('manual-match')
  async manualMatch(
    @Body()
    body: {
      line_id: string;
      shipment_id: string;
    },
    @Req() req: any,
  ) {
    const tenantId = req.user.tenant_id;

    await this.matcherService.manualMatch(
      body.line_id,
      body.shipment_id,
      tenantId,
    );

    return {
      success: true,
      message: 'Match created successfully',
    };
  }

  /**
   * Remove match from invoice line
   * POST /invoices/unmatch
   */
  @Post('unmatch')
  async unmatch(
    @Body() body: { line_id: string },
    @Req() req: any,
  ) {
    const tenantId = req.user.tenant_id;

    await this.matcherService.unmatch(body.line_id, tenantId);

    return {
      success: true,
      message: 'Match removed successfully',
    };
  }

  /**
   * Get matching statistics for a project
   * GET /invoices/matching-stats?projectId=xxx
   */
  @Get('matching-stats')
  async getMatchingStats(
    @Query('projectId') projectId: string,
    @Req() req: any,
  ) {
    const tenantId = req.user.tenant_id;

    const stats = await this.matcherService.getProjectMatchingStats(
      projectId,
      tenantId,
    );

    return {
      success: true,
      data: stats,
    };
  }
}

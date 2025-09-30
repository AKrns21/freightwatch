import { Injectable, UnauthorizedException } from '@nestjs/common';
import { PassportStrategy } from '@nestjs/passport';
import { ExtractJwt, Strategy } from 'passport-jwt';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { User } from './user.entity';

interface JwtPayload {
  sub: string;
  email: string;
  tenantId: string;
  roles: string[];
  firstName?: string;
  lastName?: string;
}

@Injectable()
export class JwtStrategy extends PassportStrategy(Strategy) {
  constructor(
    @InjectRepository(User)
    private readonly userRepository: Repository<User>,
  ) {
    super({
      jwtFromRequest: ExtractJwt.fromAuthHeaderAsBearerToken(),
      ignoreExpiration: false,
      secretOrKey: process.env.JWT_SECRET || 'dev-secret',
    });
  }

  async validate(payload: JwtPayload): Promise<User> {
    if (!payload.tenantId) {
      throw new UnauthorizedException('JWT token missing tenantId');
    }

    const user = await this.userRepository.findOne({
      where: { 
        id: payload.sub,
        email: payload.email,
        tenant_id: payload.tenantId,
        is_active: true 
      },
    });

    if (!user) {
      throw new UnauthorizedException('User not found or inactive');
    }

    return user;
  }
}
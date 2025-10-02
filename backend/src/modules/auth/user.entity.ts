import {
  Entity,
  Column,
  PrimaryGeneratedColumn,
  CreateDateColumn,
  UpdateDateColumn,
} from 'typeorm';

/**
 * User Entity for Authentication
 *
 * Note: This is a simple entity for MVP. In production, consider:
 * - User roles and permissions system
 * - Account verification/activation
 * - Password reset functionality
 * - Multi-factor authentication
 * - Account lockout after failed attempts
 */
@Entity('users')
export class User {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ unique: true })
  email: string;

  @Column()
  password_hash: string;

  @Column({ nullable: true })
  first_name: string;

  @Column({ nullable: true })
  last_name: string;

  /**
   * CRITICAL: Tenant association for multi-tenant isolation
   * Every user MUST belong to exactly one tenant
   */
  @Column('uuid')
  tenant_id: string;

  @Column({ type: 'jsonb', nullable: true })
  roles: string[]; // ['admin', 'user', 'viewer']

  @Column({ default: true })
  is_active: boolean;

  @Column({ nullable: true })
  last_login_at: Date;

  @CreateDateColumn()
  created_at: Date;

  @UpdateDateColumn()
  updated_at: Date;

  /**
   * Get full name for display
   */
  get full_name(): string {
    if (this.first_name && this.last_name) {
      return `${this.first_name} ${this.last_name}`;
    }
    return this.email;
  }

  /**
   * Check if user has a specific role
   */
  hasRole(role: string): boolean {
    return this.roles?.includes(role) || false;
  }

  /**
   * Check if user is admin
   */
  isAdmin(): boolean {
    return this.hasRole('admin');
  }

  /**
   * Convert to safe object for JWT payload (no password)
   */
  toJwtPayload(): {
    sub: string;
    email: string;
    tenantId: string;
    roles: string[];
    firstName: string | null;
    lastName: string | null;
  } {
    return {
      sub: this.id,
      email: this.email,
      tenantId: this.tenant_id,
      roles: this.roles || [],
      firstName: this.first_name,
      lastName: this.last_name,
    };
  }

  /**
   * Convert to safe object for API responses (no password)
   */
  toSafeObject(): Omit<User, 'password_hash'> {
    const { password_hash: _password_hash, ...safe } = this;
    return safe as Omit<User, 'password_hash'>;
  }
}

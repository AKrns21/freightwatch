import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { FleetVehicle } from './entities/fleet-vehicle.entity';
import { FleetDriver } from './entities/fleet-driver.entity';
import { OwnTour } from './entities/own-tour.entity';
import { OwnTourStop } from './entities/own-tour-stop.entity';

/**
 * FleetModule — own delivery fleet master data and tour records.
 *
 * Opt-in module: only activated when own_tour data is uploaded for a project.
 * Provides the data foundation for the Own vs. Carrier Benchmark (issue #26).
 */
@Module({
  imports: [
    TypeOrmModule.forFeature([FleetVehicle, FleetDriver, OwnTour, OwnTourStop]),
  ],
  exports: [TypeOrmModule],
})
export class FleetModule {}
